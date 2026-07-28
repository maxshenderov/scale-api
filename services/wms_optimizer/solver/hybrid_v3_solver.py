import time
import logging
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from ortools.sat.python import cp_model
from app.schemas import OptimizationRequest, OptimizationResponse, PlacementResult
from app.core.constants import GAP_WIDTH_MM

logger = logging.getLogger(__name__)

class HybridV3Solver:
    """
    Hybrid V3: BFD + Chain-Swap Local Search + Micro CP-SAT.
    Исправляет проблему V2, гарантируя, что вытесненные паллеты всегда находят новое место.
    """

    def __init__(self, request: OptimizationRequest):
        self.request = request
        self.sections = request.sections
        self.pallets = request.pallets
        self.existing_placements = request.existing_placements or []
        
        self.section_map = {s.id: s for s in self.sections}
        self.pallet_map = {p.id: p for p in self.pallets}
        self.existing_ids = {p.pallet_id for p in self.existing_placements}
        
        self.section_state = {}
        self.placements = []
        
        # Кэш совместимости для ускорения поиска цепочек
        self.compatible_sections_cache = {}

    def solve(self) -> OptimizationResponse:
        start_time = time.time()
        logger.info("Hybrid V3: Запуск алгоритма с Chain-Swap...")

        self._init_section_states()
        self._precompute_compatibility()

        # ФАЗА 1: Быстрая жадная эвристика (Best-Fit Decreasing)
        self._phase_bfd()
        logger.info(f"Hybrid V3: После BFD размещено {len(self.placements)}/{len(self.request.pallets)}")

        # ФАЗА 2: Цепочка перемещений (Chain-Swap Local Search)
        self._phase_chain_swap()
        logger.info(f"Hybrid V3: После Chain-Swap размещено {len(self.placements)}/{len(self.request.pallets)}")

        # ФАЗА 3: Микро CP-SAT для самых упрямых остатков
        self._phase_micro_cpsat()
        
        elapsed = time.time() - start_time
        logger.info(f"Hybrid V3: Итог {len(self.placements)}/{len(self.request.pallets)} за {elapsed:.2f}с")
        return self._build_response(elapsed)

    def _init_section_states(self):
        for sec in self.sections:
            used_width = sum(p.width + GAP_WIDTH_MM for p in self.existing_placements if p.section_id == sec.id)
            used_count = sum(1 for p in self.existing_placements if p.section_id == sec.id)
            
            self.section_state[sec.id] = {
                "free_width": sec.width - used_width,
                "free_count": sec.max_pallets - used_count,
                "current_pallets": [p.pallet_id for p in self.existing_placements if p.section_id == sec.id]
            }
            
        self.placements = [
            PlacementResult(pallet_id=p.pallet_id, section_id=p.section_id, is_new=False)
            for p in self.existing_placements
        ]

    def _precompute_compatibility(self):
        """Предварительно вычисляет, в какие секции может физически встать каждая паллета."""
        for p in self.pallets:
            compatible = []
            for sec in self.sections:
                if (p.height <= sec.max_height and p.depth <= sec.max_depth and 
                    p.weight <= sec.max_weight and 
                    (not sec.is_narrow_aisle or p.is_narrow_aisle)):
                    compatible.append(sec.id)
            self.compatible_sections_cache[p.id] = compatible

    def _phase_bfd(self):
        """Best-Fit Decreasing: сортируем по убыванию ширины и ищем минимальный остаток."""
        sorted_pallets = sorted(self.pallets, key=lambda p: (-p.width, -p.weight))
        placed_ids = set()

        for pallet in sorted_pallets:
            best_sec_id = None
            min_remaining = float('inf')
            
            for sec_id in self.compatible_sections_cache[pallet.id]:
                state = self.section_state[sec_id]
                if state["free_count"] <= 0:
                    continue
                    
                gap = GAP_WIDTH_MM if state["current_pallets"] else 0
                req_width = pallet.width + gap
                
                if state["free_width"] >= req_width:
                    remaining = state["free_width"] - req_width
                    if remaining < min_remaining:
                        min_remaining = remaining
                        best_sec_id = sec_id
            
            if best_sec_id:
                self._place_pallet(pallet.id, best_sec_id)
                placed_ids.add(pallet.id)

        self.pallets = [p for p in self.pallets if p.id not in placed_ids]

    def _phase_chain_swap(self):
        """
        Локальный поиск с цепочками перемещений (глубина 2).
        Гарантирует, что вытесненная паллета всегда найдет новое место.
        """
        if not self.pallets:
            return

        # Сортируем хвосты: сначала самые широкие (их сложнее всего пристроить)
        leftovers = sorted(self.pallets, key=lambda p: (-p.width, -p.weight))
        self.pallets = []
        
        improved = True
        while improved and leftovers:
            improved = False
            next_leftovers = []
            
            for leftover in leftovers:
                chain = self._find_chain(leftover, max_depth=2)
                if chain:
                    self._execute_chain(chain)
                    improved = True
                else:
                    next_leftovers.append(leftover)
            
            leftovers = next_leftovers

        self.pallets = leftovers

    def _find_chain(self, leftover, max_depth) -> Optional[List[Tuple]]:
        """Ищет валидную цепочку перемещений для размещения leftover."""
        req_leftover = leftover.width + GAP_WIDTH_MM
        
        for sec_a_id in self.compatible_sections_cache[leftover.id]:
            state_a = self.section_state[sec_a_id]
            if state_a["free_count"] <= 0:
                continue
                
            # Вариант 1: Влезает сразу (глубина 0)
            if state_a["free_width"] >= req_leftover:
                return [('place', leftover.id, sec_a_id)]
            
            # Вариант 2: Вытеснение 1 паллеты (глубина 1)
            if max_depth >= 1:
                for p1_id in state_a["current_pallets"]:
                    if p1_id in self.existing_ids: continue
                    p1 = self.pallet_map[p1_id]
                    
                    # Условие: после удаления p1 и добавления leftover место не уйдет в минус
                    # (GAP упрощаем для скорости эвристики)
                    if state_a["free_width"] + p1.width - leftover.width < 0:
                        continue
                        
                    for sec_b_id in self.compatible_sections_cache[p1.id]:
                        if sec_b_id == sec_a_id: continue
                        state_b = self.section_state[sec_b_id]
                        if state_b["free_count"] <= 0: continue
                        
                        req_p1 = p1.width + (GAP_WIDTH_MM if state_b["current_pallets"] else 0)
                        
                        # p1 влезает в B сразу
                        if state_b["free_width"] >= req_p1:
                            return [
                                ('remove', p1_id, sec_a_id),
                                ('place', leftover.id, sec_a_id),
                                ('place', p1_id, sec_b_id)
                            ]
                        
                        # Вариант 3: Вытеснение 2 паллет (глубина 2)
                        if max_depth >= 2:
                            for p2_id in state_b["current_pallets"]:
                                if p2_id in self.existing_ids: continue
                                p2 = self.pallet_map[p2_id]
                                
                                if state_b["free_width"] + p2.width - p1.width < 0:
                                    continue
                                    
                                for sec_c_id in self.compatible_sections_cache[p2.id]:
                                    if sec_c_id in (sec_a_id, sec_b_id): continue
                                    state_c = self.section_state[sec_c_id]
                                    if state_c["free_count"] <= 0: continue
                                    
                                    req_p2 = p2.width + (GAP_WIDTH_MM if state_c["current_pallets"] else 0)
                                    if state_c["free_width"] >= req_p2:
                                        return [
                                            ('remove', p1_id, sec_a_id),
                                            ('remove', p2_id, sec_b_id),
                                            ('place', leftover.id, sec_a_id),
                                            ('place', p1_id, sec_b_id),
                                            ('place', p2_id, sec_c_id)
                                        ]
        return None

    def _execute_chain(self, chain: List[Tuple]):
        """Выполняет цепочку перемещений. Сначала все remove, потом все place."""
        # Сначала удаляем, чтобы освободить место
        for action, p_id, sec_id in chain:
            if action == 'remove':
                self._remove_pallet(p_id, sec_id)
        
        # Потом размещаем
        for action, p_id, sec_id in chain:
            if action == 'place':
                self._place_pallet(p_id, sec_id)

    def _phase_micro_cpsat(self):
        """Точная добивка для оставшихся хвостов (таймаут 10 сек)."""
        if not self.pallets or len(self.pallets) > 200: 
            return # Если хвостов слишком много, CP-SAT зависнет, полагаемся на Chain-Swap

        model = cp_model.CpModel()
        x = {}
        
        for p in self.pallets:
            for sec_id in self.compatible_sections_cache[p.id]:
                x[(p.id, sec_id)] = model.NewBoolVar(f"x_{p.id}_{sec_id}")

        for p in self.pallets:
            vars_in = [x[(p.id, s)] for s in self.compatible_sections_cache[p.id]]
            if vars_in: model.Add(sum(vars_in) <= 1)

        for sec in self.sections:
            state = self.section_state[sec.id]
            vars_in_sec = [x[(p.id, sec.id)] for p in self.pallets if (p.id, sec.id) in x]
            if vars_in_sec:
                model.Add(sum(vars_in_sec) <= state["free_count"])
                width_expr = sum(p.width * x[(p.id, sec.id)] for p in self.pallets if (p.id, sec.id) in x)
                model.Add(width_expr <= state["free_width"])

        model.Maximize(sum(x.values()))
        
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10.0
        solver.parameters.num_search_workers = 4
        
        if solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    self._place_pallet(p_id, sec_id)

    def _place_pallet(self, pallet_id: str, section_id: str):
        p = self.pallet_map[pallet_id]
        state = self.section_state[section_id]
        gap = GAP_WIDTH_MM if state["current_pallets"] else 0
        
        state["free_width"] -= (p.width + gap)
        state["free_count"] -= 1
        state["current_pallets"].append(pallet_id)
        self.placements.append(PlacementResult(pallet_id=pallet_id, section_id=section_id, is_new=True))

    def _remove_pallet(self, pallet_id: str, section_id: str):
        p = self.pallet_map[pallet_id]
        state = self.section_state[section_id]
        
        state["free_width"] += (p.width + GAP_WIDTH_MM)
        state["free_count"] += 1
        if pallet_id in state["current_pallets"]:
            state["current_pallets"].remove(pallet_id)
            
        self.placements = [pl for pl in self.placements if not (pl.pallet_id == pallet_id and pl.section_id == section_id)]

    def _build_response(self, elapsed_time: float) -> OptimizationResponse:
        placed_ids = {pl.pallet_id for pl in self.placements}
        unplaced = [p for p in self.request.pallets if p.id not in placed_ids]
        
        return OptimizationResponse(
            placements=self.placements,
            unplaced_pallet_ids=[p.id for p in unplaced],
            metrics={
                "total_pallets": len(self.request.pallets),
                "placed_pallets": len(self.placements),
                "unplaced_pallets": len(unplaced),
                "placement_rate": len(self.placements) / len(self.request.pallets) if self.request.pallets else 0,
                "algorithm_version": "hybrid_v3",
                "execution_time_sec": round(elapsed_time, 2)
            }
        )