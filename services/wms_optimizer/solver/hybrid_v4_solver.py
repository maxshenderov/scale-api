import time
import logging
from typing import List, Dict, Set, Tuple
from collections import defaultdict

from ortools.sat.python import cp_model
from app.schemas import OptimizationRequest, OptimizationResponse, PlacementResult
from app.core.constants import GAP_WIDTH_MM

# Импортируем V3, чтобы не дублировать код эвристик
from solver.hybrid_v3_solver import HybridV3Solver 

logger = logging.getLogger(__name__)

class HybridV4Solver:
    """
    Hybrid V4: LNS (Large Neighborhood Search) / Fix-and-Optimize.
    Использует V3 для быстрого старта, затем точечно "размораживает" 
    пространство для CP-SAT, чтобы добить сложные хвосты.
    """

    def __init__(self, request: OptimizationRequest):
        self.request = request
        self.sections = request.sections
        self.pallets = request.pallets
        self.existing_placements = request.existing_placements or []
        
        self.section_map = {s.id: s for s in self.sections}
        self.pallet_map = {p.id: p for p in self.pallets}
        
        # Кэш совместимости (габариты)
        self.compatible_sections_cache = {}
        for p in self.pallets:
            self.compatible_sections_cache[p.id] = [
                s.id for s in self.sections 
                if p.height <= s.max_height and p.depth <= s.max_depth and 
                   p.weight <= s.max_weight and (not s.is_narrow_aisle or p.is_narrow_aisle)
            ]

    def solve(self) -> OptimizationResponse:
        start_time = time.time()
        logger.info("Hybrid V4: Запуск LNS (Fix-and-Optimize)...")

        # ФАЗА 1: Быстрый старт через V3 (BFD + Chain-Swap)
        v3_solver = HybridV3Solver(self.request)
        v3_response = v3_solver.solve()
        
        current_placements = {pl.pallet_id: pl.section_id for pl in v3_response.placements if pl.is_new}
        leftovers = [p for p in self.pallets if p.id not in current_placements]
        
        logger.info(f"Hybrid V4: После V3 размещено {len(current_placements)}, хвостов: {len(leftovers)}")

        # ФАЗА 2: Итеративный LNS (Fix-and-Optimize)
        max_lns_time = 20.0 # Жесткий лимит на весь LNS
        lns_start = time.time()
        iteration = 0
        
        while leftovers and (time.time() - lns_start) < max_lns_time:
            iteration += 1
            improved_count = self._lns_iteration(current_placements, leftovers)
            
            if improved_count == 0:
                break # CP-SAT не смог найти улучшений в этой зоне
                
            # Обновляем хвосты
            placed_in_iter = {p_id for p_id, sec_id in current_placements.items() if p_id in {p.id for p in leftovers}}
            leftovers = [p for p in leftovers if p.id not in placed_in_iter]
            logger.info(f"Hybrid V4: LNS итерация {iteration}. Размещено +{improved_count}, осталось хвостов: {len(leftovers)}")

        # Формируем итоговый ответ
        elapsed = time.time() - start_time
        final_placements = [
            PlacementResult(pallet_id=p_id, section_id=sec_id, is_new=True)
            for p_id, sec_id in current_placements.items()
        ]
        # Добавляем существующие (не новые)
        final_placements.extend([
            PlacementResult(pallet_id=p.pallet_id, section_id=p.section_id, is_new=False)
            for p in self.existing_placements
        ])

        unplaced = [p for p in self.pallets if p.id not in current_placements and p.id not in {ep.pallet_id for ep in self.existing_placements}]
        
        return OptimizationResponse(
            placements=final_placements,
            unplaced_pallet_ids=[p.id for p in unplaced],
            metrics={
                "total_pallets": len(self.request.pallets),
                "placed_pallets": len(final_placements),
                "unplaced_pallets": len(unplaced),
                "placement_rate": len(final_placements) / len(self.request.pallets) if self.request.pallets else 0,
                "algorithm_version": "hybrid_v4_lns",
                "execution_time_sec": round(elapsed, 2),
                "lns_iterations": iteration
            }
        )

    def _lns_iteration(self, current_placements: Dict[str, str], leftovers: List) -> int:
        """Одна итерация Fix-and-Optimize."""
        
        # 1. Определяем "Зону влияния" (целевые секции)
        target_sections = set()
        for p in leftovers:
            target_sections.update(self.compatible_sections_cache[p.id])
            
        # 2. Определяем "Размороженные" паллеты (хвосты + те, что стоят в целевых секциях)
        unfrozen_pallet_ids = {p.id for p in leftovers}
        for p_id, sec_id in current_placements.items():
            if sec_id in target_sections:
                unfrozen_pallet_ids.add(p_id)
                
        # 3. "Замороженные" паллеты (их мы не трогаем, они уже стоят хорошо)
        frozen_placements = {p_id: sec_id for p_id, sec_id in current_placements.items() if p_id not in unfrozen_pallet_ids}
        
        # 4. Считаем остаточное состояние секций с учетом замороженных
        section_state = {}
        for sec in self.sections:
            used_width = sum(self.pallet_map[pid].width + GAP_WIDTH_MM for pid, sid in frozen_placements.items() if sid == sec.id)
            used_count = sum(1 for pid, sid in frozen_placements.items() if sid == sec.id)
            
            # Учитываем изначальные existing_placements
            existing_width = sum(p.width + GAP_WIDTH_MM for p in self.existing_placements if p.section_id == sec.id)
            existing_count = sum(1 for p in self.existing_placements if p.section_id == sec.id)
            
            section_state[sec.id] = {
                "free_width": sec.width - used_width - existing_width,
                "free_count": sec.max_pallets - used_count - existing_count
            }

        # 5. Строим микро-модель CP-SAT только для размороженных
        model = cp_model.CpModel()
        unfrozen_pallets = [self.pallet_map[pid] for pid in unfrozen_pallet_ids]
        
        x = {}
        for p in unfrozen_pallets:
            for sec_id in self.compatible_sections_cache[p.id]:
                if sec_id in target_sections: # Размещаем только в целевые секции
                    x[(p.id, sec_id)] = model.NewBoolVar(f"x_{p.id}_{sec_id}")

        # Ограничение: каждая паллета максимум в одной секции
        for p in unfrozen_pallets:
            vars_in = [x[(p.id, s)] for s in target_sections if (p.id, s) in x]
            if vars_in:
                model.Add(sum(vars_in) <= 1)

        # Ограничение: вместимость секций (с учетом замороженных)
        for sec_id in target_sections:
            state = section_state[sec_id]
            vars_in_sec = [x[(p.id, sec_id)] for p in unfrozen_pallets if (p.id, sec_id) in x]
            if vars_in_sec:
                model.Add(sum(vars_in_sec) <= state["free_count"])
                
                width_expr = sum(p.width * x[(p.id, sec_id)] for p in unfrozen_pallets if (p.id, sec_id) in x)
                # Упрощение: GAP считаем фиксированным для скорости, это допустимо для LNS
                model.Add(width_expr <= state["free_width"])

        # Целевая функция: максимизировать количество размещенных ХВОСТОВ
        # (нам не важно перекладывать уже размещенные, нам важно пристроить leftovers)
        leftover_ids = {p.id for p in leftovers}
        objective_vars = [x[(p.id, s)] for p in unfrozen_pallets if p.id in leftover_ids for s in target_sections if (p.id, s) in x]
        model.Maximize(sum(objective_vars))

        # 6. Теплый старт (Warm Start) от V3
        for p in unfrozen_pallets:
            if p.id in current_placements:
                sec_id = current_placements[p.id]
                if (p.id, sec_id) in x:
                    model.AddHint(x[(p.id, sec_id)], 1)

        # 7. Решаем
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0 # 5 секунд на итерацию
        solver.parameters.num_search_workers = 4
        solver.parameters.log_search_progress = False
        
        status = solver.Solve(model)
        
        improved_count = 0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Применяем изменения
            # Сначала удаляем старые размещения размороженных паллет
            for p_id in list(unrozen_pallet_ids):
                if p_id in current_placements:
                    del current_placements[p_id]
            
            # Добавляем новые
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    current_placements[p_id] = sec_id
                    if p_id in leftover_ids:
                        improved_count += 1
                        
        return improved_count