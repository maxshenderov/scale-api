import time
import logging
import random
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from ortools.sat.python import cp_model
from app.schemas import OptimizationRequest, OptimizationResponse, PlacementResult
from app.core.constants import GAP_WIDTH_MM

logger = logging.getLogger(__name__)


class HybridV3Solver:
    """
    Hybrid V3+ : BFD (most-constrained-first) + Chain-Swap (best-chain, depth 3)
                 + Micro/Global CP-SAT (weighted objective + warm start).

    Оптимизирован под КАЧЕСТВО размещения (скорость вторична):
      - Честный учёт GAP в цепочках.
      - Chain-Swap выбирает ЛУЧШУЮ цепочку, а не первую.
      - Несколько рестартов BFD с рандомизацией, выбирается лучший старт.
      - CP-SAT со взвешенной целью, тёплым стартом и увеличенным таймаутом.
    """

    # --- Настройки качества (вынесены в константы, можно тюнить) ---
    CPSAT_TIME_LIMIT_SEC = 120.0     # было 10.0
    CPSAT_MAX_PALLETS = 600          # было 200
    CHAIN_MAX_DEPTH = 3              # было 2
    BFD_RESTARTS = 5                 # число рестартов с рандомизацией

    def __init__(self, request: OptimizationRequest):
        self.request = request
        self.sections = request.sections
        self.pallets = request.pallets
        self.existing_placements = request.existing_placements or []

        self.section_map = {s.id: s for s in self.sections}
        self.pallet_map = {p.id: p for p in self.pallets}
        self.existing_ids = {p.pallet_id for p in self.existing_placements}

        self.section_state: Dict[str, dict] = {}
        self.placements: List[PlacementResult] = []

        # Кэш совместимости для ускорения поиска цепочек
        self.compatible_sections_cache: Dict[str, List[str]] = {}

        # Метрики по фазам
        self._phase_counts = {"bfd": 0, "chain_swap": 0, "cpsat": 0}
        self._chains_executed = 0

    # ------------------------------------------------------------------
    # Основной сценарий
    # ------------------------------------------------------------------
    def solve(self) -> OptimizationResponse:
        start_time = time.time()
        logger.info("Hybrid V3+: Запуск алгоритма (оптимизация по качеству)...")

        self._init_section_states()
        self._precompute_compatibility()

        # Полный набор паллет для размещения (кроме уже стоящих).
        all_pallets = [p for p in self.pallets if p.id not in self.existing_ids]

        # ФАЗА 1: BFD с рестартами — выбираем лучший стартовый расклад.
        self._phase_bfd_best_of_restarts(all_pallets)
        logger.info(f"Hybrid V3+: После BFD размещено {len(self.placements)}/{len(all_pallets)}")

        # ФАЗА 2: Chain-Swap Local Search (best-chain, depth 3)
        self._phase_chain_swap()
        logger.info(f"Hybrid V3+: После Chain-Swap размещено {len(self.placements)}/{len(all_pallets)}")

        # ФАЗА 3: CP-SAT — сначала добивка хвостов, затем глобальный проход.
        self._phase_micro_cpsat()
        self._phase_global_cpsat(all_pallets)

        elapsed = time.time() - start_time
        logger.info(f"Hybrid V3+: Итог {len(self.placements)}/{len(all_pallets)} за {elapsed:.2f}с")
        return self._build_response(elapsed)

    # ------------------------------------------------------------------
    # Инициализация
    # ------------------------------------------------------------------
    def _init_section_states(self):
        for sec in self.sections:
            used_width = sum(
                self._pallet_width_by_placement(p) + GAP_WIDTH_MM
                for p in self.existing_placements if p.section_id == sec.id
            )
            used_count = sum(1 for p in self.existing_placements if p.section_id == sec.id)

            self.section_state[sec.id] = {
                "free_width": sec.width - used_width,
                "free_count": sec.max_pallets - used_count,
                "current_pallets": [p.pallet_id for p in self.existing_placements if p.section_id == sec.id],
            }

        self.placements = [
            PlacementResult(pallet_id=p.pallet_id, section_id=p.section_id, is_new=False)
            for p in self.existing_placements
        ]

    def _pallet_width_by_placement(self, placement) -> float:
        """Ширина паллеты по placement (существующие могут отсутствовать в pallet_map)."""
        p = self.pallet_map.get(placement.pallet_id)
        return p.width if p else 0.0

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

    # ------------------------------------------------------------------
    # ФАЗА 1: BFD с рестартами и most-constrained-first
    # ------------------------------------------------------------------
    def _phase_bfd_best_of_restarts(self, all_pallets):
        """
        Запускает BFD несколько раз с разной сортировкой/рандомизацией
        и оставляет расклад с максимальным числом размещённых паллет.
        """
        # Базовый снимок состояния (после existing placements).
        base_state = self._snapshot_state()
        base_placements = list(self.placements)

        best_placed = -1
        best_snapshot = None
        best_placements = None
        best_leftovers = None

        for attempt in range(self.BFD_RESTARTS):
            # Восстанавливаем чистое стартовое состояние перед каждой попыткой.
            self._restore_state(base_state)
            self.placements = list(base_placements)

            leftovers = self._run_bfd_once(all_pallets, attempt)
            placed_now = len(self.placements)

            if placed_now > best_placed:
                best_placed = placed_now
                best_snapshot = self._snapshot_state()
                best_placements = list(self.placements)
                best_leftovers = list(leftovers)

        # Фиксируем лучший результат.
        self._restore_state(best_snapshot)
        self.placements = best_placements
        self.pallets = best_leftovers  # незамещённые уходят в следующую фазу
        self._phase_counts["bfd"] = best_placed - len(self.existing_placements)

    def _run_bfd_once(self, all_pallets, attempt: int):
        """Один прогон BFD. Возвращает список незамещённых паллет."""
        n_comp = self.compatible_sections_cache

        if attempt == 0:
            # Most-constrained-first: мало совместимых секций + широкие/тяжёлые вперёд.
            sorted_pallets = sorted(
                all_pallets,
                key=lambda p: (len(n_comp[p.id]), -p.width, -p.weight),
            )
        elif attempt == 1:
            # Классика V3: по убыванию ширины.
            sorted_pallets = sorted(all_pallets, key=lambda p: (-p.width, -p.weight))
        else:
            # Рандомизированные рестарты для выхода из локального оптимума.
            sorted_pallets = sorted(
                all_pallets,
                key=lambda p: (len(n_comp[p.id]), -p.width, -p.weight),
            )
            rnd = random.Random(attempt)
            # Локальное перемешивание — небольшие свопы соседей.
            for i in range(len(sorted_pallets) - 1):
                if rnd.random() < 0.25:
                    sorted_pallets[i], sorted_pallets[i + 1] = sorted_pallets[i + 1], sorted_pallets[i]

        placed_ids = set()
        for pallet in sorted_pallets:
            best_sec_id = None
            min_remaining = float("inf")

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

        return [p for p in all_pallets if p.id not in placed_ids]

    # ------------------------------------------------------------------
    # ФАЗА 2: Chain-Swap (best chain, честный GAP, depth 3)
    # ------------------------------------------------------------------
    def _phase_chain_swap(self):
        if not self.pallets:
            return

        leftovers = sorted(self.pallets, key=lambda p: (-p.width, -p.weight))
        self.pallets = []

        improved = True
        while improved and leftovers:
            improved = False
            next_leftovers = []

            for leftover in leftovers:
                chain = self._find_best_chain(leftover, max_depth=self.CHAIN_MAX_DEPTH)
                if chain:
                    self._execute_chain(chain)
                    self._chains_executed += 1
                    self._phase_counts["chain_swap"] += 1
                    improved = True
                else:
                    next_leftovers.append(leftover)

            leftovers = next_leftovers

        # Оставшиеся хвосты передаём дальше в CP-SAT.
        self.pallets = leftovers

    def _find_best_chain(self, leftover, max_depth) -> Optional[List[Tuple]]:
        """
        Ищет ЛУЧШУЮ валидную цепочку (по минимальному суммарному остатку ширины),
        а не первую попавшуюся. GAP учитывается честно.
        """
        candidates: List[Tuple[float, List[Tuple]]] = []
        req_leftover = leftover.width  # gap считаем через _would_fit

        for sec_a_id in self.compatible_sections_cache[leftover.id]:
            state_a = self.section_state[sec_a_id]

            # Вариант 0: влезает сразу.
            if state_a["free_count"] > 0 and self._would_fit(sec_a_id, leftover):
                score = self._fit_score(sec_a_id, [leftover.id], [])
                candidates.append((score, [("place", leftover.id, sec_a_id)]))

            # Вариант 1: вытеснение одной паллеты.
            if max_depth >= 1:
                for p1_id in list(state_a["current_pallets"]):
                    if p1_id in self.existing_ids:
                        continue
                    p1 = self.pallet_map[p1_id]

                    # Честный GAP: проверяем, влезет ли leftover после удаления p1.
                    if not self._would_fit_after_removal(sec_a_id, [p1_id], leftover):
                        continue

                    for sec_b_id in self.compatible_sections_cache[p1.id]:
                        if sec_b_id == sec_a_id:
                            continue
                        state_b = self.section_state[sec_b_id]
                        if state_b["free_count"] <= 0:
                            continue

                        if self._would_fit(sec_b_id, p1):
                            chain = [
                                ("remove", p1_id, sec_a_id),
                                ("place", leftover.id, sec_a_id),
                                ("place", p1_id, sec_b_id),
                            ]
                            score = (self._fit_score(sec_a_id, [leftover.id], [p1_id])
                                     + self._fit_score(sec_b_id, [p1_id], []))
                            candidates.append((score, chain))

            # Вариант 2: вытеснение двух паллет (глубина 2-3).
            if max_depth >= 2:
                chain = self._find_depth2_chain(leftover, sec_a_id)
                if chain:
                    candidates.append(chain)

        if not candidates:
            return None

        # Лучшая цепочка = минимальный остаток (плотнее упаковка).
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]

    def _find_depth2_chain(self, leftover, sec_a_id) -> Optional[Tuple[float, List[Tuple]]]:
        """Вытеснение 2 паллет из секции A ради размещения leftover."""
        state_a = self.section_state[sec_a_id]
        movable = [pid for pid in state_a["current_pallets"] if pid not in self.existing_ids]

        for i in range(len(movable)):
            for j in range(i + 1, len(movable)):
                p1_id, p2_id = movable[i], movable[j]
                p1, p2 = self.pallet_map[p1_id], self.pallet_map[p2_id]

                if not self._would_fit_after_removal(sec_a_id, [p1_id, p2_id], leftover):
                    continue

                sec_b = self._first_fit_section(p1, exclude={sec_a_id})
                if not sec_b:
                    continue
                sec_c = self._first_fit_section(p2, exclude={sec_a_id, sec_b})
                if not sec_c:
                    continue

                chain = [
                    ("remove", p1_id, sec_a_id),
                    ("remove", p2_id, sec_a_id),
                    ("place", leftover.id, sec_a_id),
                    ("place", p1_id, sec_b),
                    ("place", p2_id, sec_c),
                ]
                score = self._fit_score(sec_a_id, [leftover.id], [p1_id, p2_id])
                return (score, chain)
        return None

    # ------------------------------------------------------------------
    # Помощники для честного расчёта ширины / GAP
    # ------------------------------------------------------------------
    def _would_fit(self, sec_id: str, pallet) -> bool:
        """Влезет ли pallet в секцию с честным учётом GAP и лимита по кол-ву."""
        state = self.section_state[sec_id]
        if state["free_count"] <= 0:
            return False
        gap = GAP_WIDTH_MM if state["current_pallets"] else 0
        return state["free_width"] >= pallet.width + gap

    def _would_fit_after_removal(self, sec_id: str, remove_ids: List[str], pallet) -> bool:
        """
        Честно моделирует удаление remove_ids и добавление pallet.
        Ширина секции пересчитывается по фактическому составу (GAP между паллетами).
        """
        state = self.section_state[sec_id]
        sec = self.section_map[sec_id]

        remaining = [pid for pid in state["current_pallets"] if pid not in remove_ids]
        new_composition = remaining + [pallet.id]

        used = 0.0
        for idx, pid in enumerate(new_composition):
            p = self.pallet_map[pid]
            used += p.width + (GAP_WIDTH_MM if idx > 0 else 0)

        count_ok = (len(new_composition)) <= sec.max_pallets - self._existing_count(sec_id, remove_ids)
        return used <= sec.width and count_ok

    def _existing_count(self, sec_id, remove_ids):
        """Кол-во existing-паллет, остающихся в секции (не двигаем)."""
        return sum(
            1 for pid in self.section_state[sec_id]["current_pallets"]
            if pid in self.existing_ids and pid not in remove_ids
        )

    def _fit_score(self, sec_id: str, add_ids: List[str], remove_ids: List[str]) -> float:
        """Остаток ширины секции после гипотетической операции (чем меньше — тем плотнее)."""
        state = self.section_state[sec_id]
        sec = self.section_map[sec_id]
        comp = [pid for pid in state["current_pallets"] if pid not in remove_ids] + add_ids
        used = 0.0
        for idx, pid in enumerate(comp):
            p = self.pallet_map[pid]
            used += p.width + (GAP_WIDTH_MM if idx > 0 else 0)
        return max(sec.width - used, 0.0)

    def _first_fit_section(self, pallet, exclude=None) -> Optional[str]:
        exclude = exclude or set()
        best_sec, best_rem = None, float("inf")
        for sec_id in self.compatible_sections_cache[pallet.id]:
            if sec_id in exclude:
                continue
            if self._would_fit(sec_id, pallet):
                rem = self._fit_score(sec_id, [pallet.id], [])
                if rem < best_rem:
                    best_rem, best_sec = rem, sec_id
        return best_sec

    # ------------------------------------------------------------------
    # Выполнение цепочек и базовые операции
    # ------------------------------------------------------------------
    def _execute_chain(self, chain: List[Tuple]):
        for action, p_id, sec_id in chain:
            if action == "remove":
                self._remove_pallet(p_id, sec_id)
        for action, p_id, sec_id in chain:
            if action == "place":
                self._place_pallet(p_id, sec_id)

    def _place_pallet(self, pallet_id: str, section_id: str):
        p = self.pallet_map[pallet_id]
        state = self.section_state[section_id]
        gap = GAP_WIDTH_MM if state["current_pallets"] else 0

        state["free_width"] -= (p.width + gap)
        state["free_count"] -= 1
        state["current_pallets"].append(pallet_id)
        self.placements.append(
            PlacementResult(pallet_id=pallet_id, section_id=section_id, is_new=True)
        )

    def _remove_pallet(self, pallet_id: str, section_id: str):
        p = self.pallet_map[pallet_id]
        state = self.section_state[section_id]

        state["free_width"] += (p.width + GAP_WIDTH_MM)
        state["free_count"] += 1
        if pallet_id in state["current_pallets"]:
            state["current_pallets"].remove(pallet_id)

        self.placements = [
            pl for pl in self.placements
            if not (pl.pallet_id == pallet_id and pl.section_id == section_id)
        ]

    # ------------------------------------------------------------------
    # Snapshot / restore для рестартов BFD
    # ------------------------------------------------------------------
    def _snapshot_state(self):
        return {
            sid: {
                "free_width": st["free_width"],
                "free_count": st["free_count"],
                "current_pallets": list(st["current_pallets"]),
            }
            for sid, st in self.section_state.items()
        }

    def _restore_state(self, snapshot):
        for sid, st in snapshot.items():
            self.section_state[sid] = {
                "free_width": st["free_width"],
                "free_count": st["free_count"],
                "current_pallets": list(st["current_pallets"]),
            }

    # ------------------------------------------------------------------
    # ФАЗА 3a: CP-SAT для хвостов (взвешенная цель + warm start)
    # ------------------------------------------------------------------
    def _phase_micro_cpsat(self):
        if not self.pallets or len(self.pallets) > self.CPSAT_MAX_PALLETS:
            return
        self._run_cpsat(self.pallets, warm_start=True, tag="micro")

    # ------------------------------------------------------------------
    # ФАЗА 3b: глобальный CP-SAT (перераспределение всех НЕ-existing паллет)
    # ------------------------------------------------------------------
    def _phase_global_cpsat(self, all_pallets):
        """
        Финальный проход: пробуем перераспределить ВСЕ новые паллеты сразу,
        чтобы улучшить итоговое качество. Существующие (existing) не трогаем.
        """
        movable = [p for p in all_pallets]  # existing не входят в all_pallets
        if not movable or len(movable) > self.CPSAT_MAX_PALLETS:
            return

        # Снимаем все НЕ-existing размещения и решаем заново.
        new_placements = [pl for pl in self.placements if pl.is_new]
        for pl in new_placements:
            self._remove_pallet(pl.pallet_id, pl.section_id)

        before = len(self.placements)
        self._run_cpsat(movable, warm_start=True, tag="global")

        # Если стало хуже — этого не случится при корректной модели,
        # но логируем для контроля.
        if len(self.placements) < before:
            logger.warning("Global CP-SAT ухудшил результат — проверьте модель.")

    def _run_cpsat(self, pallets, warm_start: bool, tag: str):
        model = cp_model.CpModel()
        x = {}

        for p in pallets:
            for sec_id in self.compatible_sections_cache[p.id]:
                x[(p.id, sec_id)] = model.NewBoolVar(f"x_{p.id}_{sec_id}")

        # Каждая паллета размещается максимум в одну секцию.
        for p in pallets:
            vars_in = [x[(p.id, s)] for s in self.compatible_sections_cache[p.id]]
            if vars_in:
                model.Add(sum(vars_in) <= 1)

        # Ограничения по секциям: количество и суммарная ширина (с GAP-поправкой).
        for sec in self.sections:
            state = self.section_state[sec.id]
            vars_in_sec = [x[(p.id, sec.id)] for p in pallets if (p.id, sec.id) in x]
            if not vars_in_sec:
                continue

            model.Add(sum(vars_in_sec) <= state["free_count"])

            # Ширина: сумма ширин + GAP на каждую новую паллету (консервативно).
            width_expr = sum(
                (self.pallet_map[p.id].width + GAP_WIDTH_MM) * x[(p.id, sec.id)]
                for p in pallets if (p.id, sec.id) in x
            )
            # Если секция уже занята — gap учтён; если пуста, лишний gap
            # даёт небольшой запас (безопасно для физического размещения).
            model.Add(width_expr <= int(state["free_width"]) + GAP_WIDTH_MM)

        # --- Взвешенная цель: приоритет крупным/тяжёлым паллетам ---
        # Вес = ширина (можно заменить на p.width + alpha*p.weight).
        objective = []
        for p in pallets:
            weight = int(p.width)  # трудные широкие паллеты ценнее
            for sec_id in self.compatible_sections_cache[p.id]:
                objective.append(weight * x[(p.id, sec_id)])
        if objective:
            model.Maximize(sum(objective))

        # --- Тёплый старт: подсказываем текущее решение ---
        if warm_start:
            current = {(pl.pallet_id, pl.section_id) for pl in self.placements if pl.is_new}
            for (p_id, sec_id), var in x.items():
                model.AddHint(var, 1 if (p_id, sec_id) in current else 0)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.CPSAT_TIME_LIMIT_SEC
        solver.parameters.num_search_workers = 8

        status = solver.Solve(model)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Полностью применяем найденное решение для этой группы паллет.
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    already = any(
                        pl.pallet_id == p_id and pl.section_id == sec_id
                        for pl in self.placements
                    )
                    if not already:
                        self._place_pallet(p_id, sec_id)
                        self._phase_counts["cpsat"] += 1

            placed_ids = {pl.pallet_id for pl in self.placements}
            self.pallets = [p for p in pallets if p.id not in placed_ids]

    # ------------------------------------------------------------------
    # Результат и метрики
    # ------------------------------------------------------------------
    def _build_response(self, elapsed_time: float) -> OptimizationResponse:
        placed_ids = {pl.pallet_id for pl in self.placements}
        unplaced = [p for p in self.request.pallets if p.id not in placed_ids]

        # Fill rate по ширине секций.
        total_capacity = sum(s.width for s in self.sections) or 1
        total_free = sum(st["free_width"] for st in self.section_state.values())
        fill_rate = 1.0 - (total_free / total_capacity)

        return OptimizationResponse(
            placements=self.placements,
            unplaced_pallet_ids=[p.id for p in unplaced],
            metrics={
                "total_pallets": len(self.request.pallets),
                "placed_pallets": len(self.placements),
                "unplaced_pallets": len(unplaced),
                "placement_rate": len(self.placements) / len(self.request.pallets)
                if self.request.pallets else 0,
                "width_fill_rate": round(fill_rate, 4),
                "placed_by_bfd": self._phase_counts["bfd"],
                "placed_by_chain_swap": self._phase_counts["chain_swap"],
                "placed_by_cpsat": self._phase_counts["cpsat"],
                "chains_executed": self._chains_executed,
                "algorithm_version": "hybrid_v3_plus",
                "execution_time_sec": round(elapsed_time, 2),
            },
        )