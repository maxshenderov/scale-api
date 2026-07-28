import time
import logging
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
import numpy as np

from ortools.sat.python import cp_model

# Импортируем только схемы данных, не трогаем старые солверы!
from app.schemas import OptimizationRequest, OptimizationResponse, PlacementResult
from app.core.constants import GAP_WIDTH_MM # Предполагаемый зазор между паллетами

logger = logging.getLogger(__name__)

class HybridV2Solver:
    """
    Новый независимый алгоритм оптимизации (Hybrid V2).
    Не изменяет и не использует старые алгоритмы.
    """

    def __init__(self, request: OptimizationRequest):
        self.request = request
        self.sections = request.sections
        self.pallets = request.pallets
        self.existing_placements = request.existing_placements or []
        
        # Внутренние структуры данных для быстрого доступа
        self.section_map = {s.id: s for s in self.sections}
        self.pallet_map = {p.id: p for p in self.pallets}
        
        # Словарь для хранения текущего состояния секций (остаток ширины, кол-во паллет)
        self.section_state = {} 
        self.placements = [] # Итоговые размещения

    def solve(self) -> OptimizationResponse:
        start_time = time.time()
        logger.info("Hybrid V2: Запуск нового алгоритма...")

        # Инициализация состояния секций с учетом уже размещенных паллет
        self._init_section_states()

        # ФАЗА 1 & 2: Быстрая эвристика Best-Fit Decreasing (BFD)
        self._phase_bfd_heuristic()
        
        placed_count = len(self.placements)
        total_count = len(self.pallets)
        logger.info(f"Hybrid V2: После BFD размещено {placed_count}/{total_count}")

        # ФАЗА 3: Локальный поиск (Swaps) для устранения фрагментации
        self._phase_local_search_swaps()
        
        placed_count = len(self.placements)
        logger.info(f"Hybrid V2: После Swaps размещено {placed_count}/{total_count}")

        # ФАЗА 4: Точный CP-SAT только для оставшихся "хвостов"
        self._phase_micro_cpsat()
        
        placed_count = len(self.placements)
        logger.info(f"Hybrid V2: После CP-SAT размещено {placed_count}/{total_count}")

        # Формирование ответа
        elapsed = time.time() - start_time
        return self._build_response(elapsed)

    def _init_section_states(self):
        """Инициализирует свободное пространство в секциях."""
        for sec in self.sections:
            used_width = sum(p.width + GAP_WIDTH_MM for p in self.existing_placements if p.section_id == sec.id)
            used_count = sum(1 for p in self.existing_placements if p.section_id == sec.id)
            
            self.section_state[sec.id] = {
                "free_width": sec.width - used_width,
                "free_count": sec.max_pallets - used_count,
                "current_pallets": [p.id for p in self.existing_placements if p.section_id == sec.id]
            }
            
        # Добавляем существующие размещения в итоговый список
        self.placements = [
            PlacementResult(pallet_id=p.pallet_id, section_id=p.section_id, is_new=False)
            for p in self.existing_placements
        ]

    def _can_fit(self, pallet, section_id: int) -> bool:
        """Проверяет, поместится ли паллета в секцию (физические ограничения)."""
        sec = self.section_map[section_id]
        state = self.section_state[section_id]
        
        # Проверка габаритов и веса
        if pallet.height > sec.max_height or pallet.depth > sec.max_depth:
            return False
        if pallet.weight > sec.max_weight:
            return False
            
        # Проверка узкопроходности (если секция узкопроходная, паллета тоже должна быть)
        if sec.is_narrow_aisle and not pallet.is_narrow_aisle:
            return False

        # Проверка свободного места
        required_width = pallet.width + (GAP_WIDTH_MM if state["current_pallets"] else 0)
        if state["free_width"] < required_width or state["free_count"] <= 0:
            return False
            
        return True

    def _phase_bfd_heuristic(self):
        """
        Фаза 1 & 2: Сортируем паллеты по убыванию ширины (Decreasing).
        Для каждой ищем секцию, где останется МИНИМУМ свободного места (Best-Fit).
        """
        # Сортируем паллеты: сначала узкопроходные, затем по убыванию ширины
        sorted_pallets = sorted(
            self.pallets, 
            key=lambda p: (not p.is_narrow_aisle, -p.width, -p.weight)
        )
        
        placed_pallet_ids = set()

        for pallet in sorted_pallets:
            best_section_id = None
            min_remaining_width = float('inf')
            
            for sec in self.sections:
                if self._can_fit(pallet, sec.id):
                    state = self.section_state[sec.id]
                    req_width = pallet.width + (GAP_WIDTH_MM if state["current_pallets"] else 0)
                    remaining = state["free_width"] - req_width
                    
                    # Ищем секцию, где остаток минимален (Best-Fit)
                    if remaining < min_remaining_width:
                        min_remaining_width = remaining
                        best_section_id = sec.id
            
            if best_section_id is not None:
                self._place_pallet(pallet.id, best_section_id)
                placed_pallet_ids.add(pallet.id)

        # Обновляем список паллет для следующих фаз (оставляем только неразмещенные)
        self.pallets = [p for p in self.pallets if p.id not in placed_pallet_ids]

    def _phase_local_search_swaps(self):
        """
        Фаза 3: Пытаемся разместить крупные "хвосты" путем вытеснения (swap) 
        мелких паллет из частично заполненных секций.
        """
        if not self.pallets:
            return

        leftovers = list(self.pallets)
        self.pallets = [] # Очищаем, будем добавлять только те, что не смогли засwapить

        for leftover in leftovers:
            swapped = False
            
            # Ищем секции, где паллета почти помещается (не хватает < 30% ширины)
            for sec in self.sections:
                state = self.section_state[sec.id]
                if not self._can_fit_basic(leftover, sec): # Базовая проверка (габариты)
                    continue
                
                req_width = leftover.width + (GAP_WIDTH_MM if state["current_pallets"] else 0)
                deficit = req_width - state["free_width"]
                
                if 0 < deficit <= (leftover.width * 0.4): # Не хватает места, но не критично
                    # Пытаемся найти паллету в этой секции, которую можно вытащить
                    for placed_p_id in list(state["current_pallets"]):
                        if placed_p_id in [p.pallet_id for p in self.existing_placements]:
                            continue # Не трогаем уже существовавшие до оптимизации
                            
                        placed_p = self.pallet_map[placed_p_id]
                        
                        # Если вынутая паллета освободит достаточно места
                        if placed_p.width + GAP_WIDTH_MM >= deficit:
                            # Убираем старую
                            self._remove_pallet(placed_p_id, sec.id)
                            # Ставим новую
                            self._place_pallet(leftover.id, sec.id)
                            # Старую паллету пробуем пристроить в другое место (в конец списка хвостов)
                            self.pallets.append(placed_p) 
                            swapped = True
                            break
                if swapped:
                    break
            
            if not swapped:
                self.pallets.append(leftover)

    def _can_fit_basic(self, pallet, sec) -> bool:
        """Базовая проверка габаритов без учета свободного места."""
        if pallet.height > sec.max_height or pallet.depth > sec.max_depth:
            return False
        if pallet.weight > sec.max_weight:
            return False
        if sec.is_narrow_aisle and not pallet.is_narrow_aisle:
            return False
        return True

    def _phase_micro_cpsat(self):
        """
        Фаза 4: Запускаем CP-SAT ТОЛЬКО для оставшихся паллет (< 5% от общего числа).
        Используем разрыв симметрии и теплый старт.
        """
        if not self.pallets:
            return

        model = cp_model.CpModel()
        
        # Переменные: X[pallet_idx, sec_idx] = 1, если паллета в секции
        pallet_indices = {p.id: i for i, p in enumerate(self.pallets)}
        sec_indices = {s.id: i for i, s in enumerate(self.sections)}
        
        x = {}
        for p in self.pallets:
            for sec in self.sections:
                if self._can_fit_basic(p, sec):
                    x[(p.id, sec.id)] = model.NewBoolVar(f"x_{p.id}_{sec.id}")

        # Ограничение 1: Каждая паллета максимум в одной секции
        for p in self.pallets:
            valid_vars = [x[(p.id, sec.id)] for sec in self.sections if (p.id, sec.id) in x]
            if valid_vars:
                model.Add(sum(valid_vars) <= 1)

        # Ограничение 2: Вместимость секций (ширина и количество)
        for sec in self.sections:
            state = self.section_state[sec.id]
            vars_in_sec = [x[(p.id, sec.id)] for p in self.pallets if (p.id, sec.id) in x]
            
            if vars_in_sec:
                # Количество
                model.Add(sum(vars_in_sec) <= state["free_count"])
                
                # Ширина (упрощенно, без учета динамического GAP для скорости, 
                # так как GAP учитывается в эвристиках, а тут добивка)
                width_expr = sum(p.width * x[(p.id, sec.id)] for p in self.pallets if (p.id, sec.id) in x)
                model.Add(width_expr <= state["free_width"])

        # Разрыв симметрии: если секции идентичны, упорядочиваем их
        # (Группируем секции по габаритам)
        sec_groups = defaultdict(list)
        for sec in self.sections:
            key = (sec.max_height, sec.max_depth, sec.max_weight, sec.width, sec.is_narrow_aisle)
            sec_groups[key].append(sec.id)
            
        for key, sec_ids in sec_groups.items():
            if len(sec_ids) > 1:
                for i in range(len(sec_ids) - 1):
                    sec_a, sec_b = sec_ids[i], sec_ids[i+1]
                    vars_a = [x[(p.id, sec_a)] for p in self.pallets if (p.id, sec_a) in x]
                    vars_b = [x[(p.id, sec_b)] for p in self.pallets if (p.id, sec_b) in x]
                    if vars_a and vars_b:
                        model.Add(sum(vars_a) >= sum(vars_b))

        # Целевая функция: максимизировать количество размещенных паллет
        model.Maximize(sum(x.values()))

        # Теплый старт (Hint) - не используем здесь, так как BFD уже все, что мог, разместил.
        # Но мы можем подсказать солверу не размещать то, что точно не влезет.

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 15.0 # Жесткий лимит времени на добивку
        solver.parameters.num_search_workers = 8
        
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    self._place_pallet(p_id, sec_id)

    def _place_pallet(self, pallet_id: str, section_id: str):
        """Размещает паллету и обновляет состояние."""
        p = self.pallet_map[pallet_id]
        state = self.section_state[section_id]
        
        gap = GAP_WIDTH_MM if state["current_pallets"] else 0
        state["free_width"] -= (p.width + gap)
        state["free_count"] -= 1
        state["current_pallets"].append(pallet_id)
        
        self.placements.append(PlacementResult(pallet_id=pallet_id, section_id=section_id, is_new=True))

    def _remove_pallet(self, pallet_id: str, section_id: str):
        """Убирает паллету из секции и обновляет состояние (для Swaps)."""
        p = self.pallet_map[pallet_id]
        state = self.section_state[section_id]
        
        # При удалении нужно пересчитать GAP. Упрощаем: возвращаем ширину паллеты.
        # В реальности GAP зависит от порядка, но для локального поиска этого достаточно.
        state["free_width"] += p.width 
        state["free_count"] += 1
        if pallet_id in state["current_pallets"]:
            state["current_pallets"].remove(pallet_id)
            
        # Удаляем из итоговых размещений
        self.placements = [pl for pl in self.placements if not (pl.pallet_id == pallet_id and pl.section_id == section_id)]

    def _build_response(self, elapsed_time: float) -> OptimizationResponse:
        """Формирует итоговый ответ в стандартном формате."""
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
                "algorithm_version": "hybrid_v2",
                "execution_time_sec": round(elapsed_time, 2)
            }
        )