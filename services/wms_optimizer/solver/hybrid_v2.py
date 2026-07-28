"""Hybrid V2 Solver — адаптирован под реальные схемы проекта.

Четырёхфазный алгоритм:
  1. BFD (Best-Fit Decreasing) — жадное размещение
  2. Local Search (Swaps) — вытеснение мелких паллет крупными
  3. Micro CP-SAT — точный добив остатков
  4. Section Optimizer — назначение адресов внутри секций

Интегрируется с api.schemas, models.*, optimizer.potential.
"""
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from api.schemas import (
    NewPalletSchema,
    OccupancySectionSchema,
    OperationSchema,
    NotPlacedSchema,
    MetricsSchema,
    OptimizationRequest,
    OptimizationResponse,
    OptimizationSettingsSchema,
    PlacementStatus,
    SolverStatus,
)
from models.occupancy_builder import build_warehouse_state
from models.pallet import Pallet, PalletTypeSize
from models.section import Section
from optimizer.potential import section_fits_pallet

logger = logging.getLogger(__name__)


@dataclass
class _SectionState:
    """Текущее состояние секции в процессе размещения."""
    section: Section
    free_width: float
    free_count: int
    placed_pallets: List[Pallet] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.section.id

    @property
    def narrow_aisle(self) -> bool:
        return self.section.narrow_aisle


class HybridV2Solver:
    """Гибридный решатель: BFD + Swaps + Micro CP-SAT + Section Optimizer."""

    def __init__(
        self,
        occupancy: List[OccupancySectionSchema],
        new_pallets: List[NewPalletSchema],
        settings: OptimizationSettingsSchema,
    ):
        self.settings = settings
        self.time_limit = settings.timeLimitSeconds

        # Строим внутренние модели
        sections, _, existing_pallets = build_warehouse_state(occupancy)
        self.sections: List[Section] = sections

        # Новые паллеты
        self.new_pallets: List[Pallet] = [
            Pallet(
                id=p.id,
                type_size=PalletTypeSize(
                    width=p.width, height=p.height, depth=p.depth, weight=p.weight
                ),
            )
            for p in new_pallets
        ]
        self._all_pallets: List[Pallet] = list(self.new_pallets)  # копия для lookup

        # Состояние секций
        self.section_states: Dict[str, _SectionState] = {}
        self._init_section_states(existing_pallets)

        # Результаты
        self.placements: Dict[str, str] = {}  # pallet_id → section_id
        self.not_placed_reasons: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Инициализация
    # ------------------------------------------------------------------

    def _init_section_states(self, existing: List[Pallet]):
        """Инициализирует состояние секций с учётом существующих паллет."""
        # Группируем существующие по секциям
        by_section: Dict[str, List[Pallet]] = defaultdict(list)
        for p in existing:
            if p.current_section_id:
                by_section[p.current_section_id].append(p)

        for sec in self.sections:
            existing_in_sec = by_section.get(sec.id, [])
            used_width = sum(
                p.width + sec.gap_width for p in existing_in_sec
            )
            state = _SectionState(
                section=sec,
                free_width=sec.width - used_width - sec.gap_width,  # ещё один gap для нового
                free_count=sec.max_pallets - len(existing_in_sec),
                placed_pallets=list(existing_in_sec),
            )
            self.section_states[sec.id] = state

    # ------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.new_pallets)
        logger.info(f"Hybrid V2: запуск, {total} паллет, {len(self.sections)} секций")

        # Фаза 1: BFD
        n1 = self._phase_bfd()
        logger.info(f"Hybrid V2: после BFD размещено {n1}/{total} ({n1/total*100:.1f}%)")
        print(f"  [BFD]     {n1}/{total} ({n1/total*100:.1f}%)")

        # Фаза 2: Swaps (пока отключены — ухудшают результат на FFD)
        n2 = n1  # self._phase_swaps()
        logger.info(f"Hybrid V2: после Swaps размещено {n2}/{total} ({n2/total*100:.1f}%)")
        print(f"  [Swaps]   SKIPPED")

        # Фаза 3: Micro CP-SAT
        n3 = self._phase_micro_cpsat()
        logger.info(f"Hybrid V2: после CP-SAT размещено {n3}/{total} ({n3/total*100:.1f}%)")
        print(f"  [CP-SAT]  {n3}/{total} ({n3/total*100:.1f}%)")

        # Фаза 4: Назначение адресов внутри секций
        operations, not_placed = self._assign_addresses()

        elapsed = time.time() - t0
        logger.info(f"Hybrid V2: завершён за {elapsed:.1f}с, {n3}/{total} паллет")

        return self._build_response(operations, not_placed, elapsed, n3)

    # ------------------------------------------------------------------
    # Фаза 1: Best-Fit Decreasing
    # ------------------------------------------------------------------

    def _phase_bfd(self) -> int:
        """Группировка по типоразмерам: narrow→narrow, wide→wide, затем смешанный добив.

        Типоразмеры обрабатываются от крупных к мелким (высота↓→ширина↓→вес↓).
        Внутри одного типоразмера — first-fit для равномерного распределения.
        """
        # Группируем паллеты по типоразмеру
        from collections import defaultdict
        type_groups: Dict[tuple, List[Pallet]] = defaultdict(list)
        for p in self.new_pallets:
            key = (p.is_narrow, p.height, p.width, p.depth, p.weight)
            type_groups[key].append(p)

        # Сортируем группы: narrow→wide, высота↓, ширина↓, вес↓
        sorted_keys = sorted(
            type_groups.keys(),
            key=lambda k: (not k[0], -k[1], -k[2], -k[4]),
        )

        narrow_states = [s for s in self.section_states.values() if s.narrow_aisle]
        wide_states = [s for s in self.section_states.values() if not s.narrow_aisle]

        logger.info(
            f"  Типоразмеров: {len(type_groups)}, "
            f"narrow секций: {len(narrow_states)}, wide: {len(wide_states)}"
        )

        placed_ids = set()

        for key in sorted_keys:
            is_narrow, h, w, d, wg = key
            pallets_in_group = type_groups[key]

            # Определяем целевые секции
            if is_narrow:
                targets = narrow_states + wide_states  # narrow → narrow first, then wide
            else:
                targets = wide_states  # wide → wide only (narrow sections can't fit wide pallets)

            for pallet in sorted(pallets_in_group, key=lambda p: -p.width):
                sec_id = self._find_best_fit_in(pallet, targets)
                if sec_id:
                    self._do_place(pallet.id, sec_id)
                    placed_ids.add(pallet.id)

        total = len(placed_ids)
        # Статистика по narrow/wide
        narrow_placed = sum(1 for pid in placed_ids
                          for p in self.new_pallets if p.id == pid and p.is_narrow)
        wide_placed = total - narrow_placed
        all_narrow = sum(1 for p in self.new_pallets if p.is_narrow)
        all_wide = len(self.new_pallets) - all_narrow
        print(f"  narrow: {narrow_placed}/{all_narrow}, wide: {wide_placed}/{all_wide}")

        self.new_pallets = [p for p in self.new_pallets if p.id not in placed_ids]
        return total

    def _find_first_fit_in(self, pallet: Pallet, section_states: list) -> Optional[str]:
        """First-fit в заданном списке секций."""
        for state in section_states:
            if state.free_count <= 0:
                continue
            if section_fits_pallet(state.section, state.placed_pallets, pallet):
                return state.id
        return None

    def _find_best_fit_in(self, pallet: Pallet, section_states: list) -> Optional[str]:
        """Best-fit в заданном списке секций — минимальный остаток ширины."""
        best_id = None
        min_remaining = float("inf")
        for state in section_states:
            if state.free_count <= 0:
                continue
            if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                continue
            gap = state.section.gap_width
            remaining = state.free_width - (pallet.width + gap)
            if remaining < min_remaining:
                min_remaining = remaining
                best_id = state.id
        return best_id

    def _find_best_fit(self, pallet: Pallet) -> Optional[str]:
        """Ищет секцию с минимальным остатком ширины после размещения (best-fit)."""
        best_id = None
        min_remaining = float("inf")

        for state in self.section_states.values():
            if state.free_count <= 0:
                continue
            if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                continue

            # Оцениваем остаток после размещения
            gap = state.section.gap_width
            required = pallet.width + gap
            remaining = state.free_width - required

            if remaining < min_remaining:
                min_remaining = remaining
                best_id = state.id

        return best_id

    def _find_first_fit(self, pallet: Pallet) -> Optional[str]:
        """Ищет первую подходящую секцию (first-fit) — быстрее и равномернее распределяет."""
        for state in self.section_states.values():
            if state.free_count <= 0:
                continue
            if section_fits_pallet(state.section, state.placed_pallets, pallet):
                return state.id
        return None

    # ------------------------------------------------------------------
    # Фаза 2: Local Search (Swaps)
    # ------------------------------------------------------------------

    def _phase_swaps(self) -> int:
        """Пытаемся разместить крупные остатки, вытесняя мелкие паллеты из секций."""
        if not self.new_pallets:
            return len(self.placements)

        leftovers = list(self.new_pallets)
        self.new_pallets = []
        swapped_count = 0

        for leftover in leftovers:
            swapped = self._try_swap(leftover)
            if swapped:
                swapped_count += 1
            else:
                self.new_pallets.append(leftover)

        logger.info(f"  Swaps: {swapped_count} успешных замен")
        return len(self.placements)

    def _try_swap(self, pallet: Pallet) -> bool:
        """Пытается разместить pallet, вытеснив более мелкие из частично заполненной секции."""
        for state in self.section_states.values():
            # Базовая совместимость (без учёта свободного места)
            if state.free_count <= 0 and not state.placed_pallets:
                continue
            if not self._basic_fits(pallet, state.section):
                continue

            gap = state.section.gap_width
            required = pallet.width + gap
            deficit = required - state.free_width

            # Нужно место, но не более 40% ширины паллета
            if deficit <= 0:
                continue  # и так помещается — BFD уже должен был разместить
            if deficit > pallet.width * 0.4:
                continue  # слишком большой дефицит

            # Ищем паллеты, которые можно вытеснить
            for placed in list(state.placed_pallets):
                # Не трогаем существовавшие до оптимизации (movable=False)
                if not placed.movable:
                    continue
                # Пропускаем паллеты больше или равные по ширине
                if placed.width >= pallet.width:
                    continue

                # Достаточно ли места освободится?
                freed = placed.width + gap
                if freed >= deficit:
                    # Вытесняем
                    self._do_remove(placed.id, state.id)
                    self._do_place(pallet.id, state.id)
                    self.new_pallets.append(placed)  # попробуем пристроить потом
                    return True

        return False

    def _basic_fits(self, pallet: Pallet, section: Section) -> bool:
        """Базовая проверка габаритов (без учёта свободного места в секции)."""
        if pallet.height > section.height:
            return False
        if pallet.depth > section.depth:
            return False
        if pallet.width > section.eff_max_width:
            return False
        if section.eff_max_depth > 0 and pallet.depth > section.eff_max_depth:
            return False
        if pallet.weight > section.max_weight:
            return False
        if section.narrow_aisle and not pallet.is_narrow:
            return False
        return True

    # ------------------------------------------------------------------
    # Фаза 3: Micro CP-SAT для остатков
    # ------------------------------------------------------------------

    def _phase_micro_cpsat(self) -> int:
        """CP-SAT только для оставшихся паллет (< 10% от общего числа)."""
        if not self.new_pallets:
            return len(self.placements)

        model = cp_model.CpModel()

        # CP-SAT работает с целыми числами — масштабируем ширину ×10
        SCALE = 10

        # Переменные X[pallet_idx, section_idx]
        states = list(self.section_states.values())

        x = {}
        for p in self.new_pallets:
            for state in states:
                if state.free_count > 0 and self._basic_fits(p, state.section):
                    x[(p.id, state.id)] = model.NewBoolVar(f"x_{p.id}_{state.id}")

        # Ограничение: каждая паллета ≤ 1 секции
        for p in self.new_pallets:
            valid = [x[(p.id, s.id)] for s in states if (p.id, s.id) in x]
            if valid:
                model.Add(sum(valid) <= 1)

        # Ограничение: вместимость секций
        for state in states:
            vars_in = [x[(p.id, state.id)] for p in self.new_pallets if (p.id, state.id) in x]
            if not vars_in:
                continue

            # Количество
            model.Add(sum(vars_in) <= state.free_count)

            # Ширина (масштабированная)
            width_expr = sum(
                int(p.width * SCALE) * x[(p.id, state.id)]
                for p in self.new_pallets
                if (p.id, state.id) in x
            )
            model.Add(width_expr <= int(state.free_width * SCALE))

        # Symmetry breaking: идентичные секции упорядочиваем
        sec_groups = defaultdict(list)
        for state in states:
            s = state.section
            key = (s.height, s.depth, s.max_weight, s.width, s.narrow_aisle)
            sec_groups[key].append(state.id)

        for sec_ids in sec_groups.values():
            if len(sec_ids) > 1:
                for i in range(len(sec_ids) - 1):
                    a, b = sec_ids[i], sec_ids[i + 1]
                    va = [x[(p.id, a)] for p in self.new_pallets if (p.id, a) in x]
                    vb = [x[(p.id, b)] for p in self.new_pallets if (p.id, b) in x]
                    if va and vb:
                        model.Add(sum(va) >= sum(vb))

        # Целевая: максимизировать размещение
        model.Maximize(sum(x.values()))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = min(60.0, self.time_limit * 0.5)
        solver.parameters.num_search_workers = 8
        solver.parameters.log_search_progress = False

        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    self._do_place(p_id, sec_id)

        # Убираем размещённые из new_pallets
        self.new_pallets = [p for p in self.new_pallets if p.id not in self.placements]
        return len(self.placements)

    # ------------------------------------------------------------------
    # Фаза 4: Назначение адресов внутри секций
    # ------------------------------------------------------------------

    def _assign_addresses(self) -> Tuple[List[OperationSchema], List[NotPlacedSchema]]:
        """Назначает адреса паллетам внутри секций по геометрическому правилу 1С."""
        all_ops: List[OperationSchema] = []

        # Группируем размещённые паллеты по секциям
        by_section: Dict[str, List[str]] = defaultdict(list)
        for p_id, sec_id in self.placements.items():
            by_section[sec_id].append(p_id)

        # Для каждой секции: сортируем паллеты по ширине↓ и назначаем адреса
        for sec_id, p_ids in by_section.items():
            state = self.section_states[sec_id]
            section = state.section

            # Сортируем по убыванию ширины (широкие → центр = позиция 2)
            sorted_ids = sorted(
                p_ids,
                key=lambda pid: next(
                    (p.width for p in self._all_pallets if p.id == pid), 0
                ),
                reverse=True,
            )

            # Генерируем адреса: <section_id>-A1, A2, A3
            # Правило 1С: паллет > 2W/3 → центр (позиция 2)
            #              паллет > W/3  → края (позиции 1, 3)
            #              паллет ≤ W/3  → любая позиция
            positions = [1, 2, 3]  # доступные позиции
            occupied = set()

            for p_id in sorted_ids:
                pallet = next((p for p in self._all_pallets if p.id == p_id), None)
                if pallet is None:
                    continue

                w = pallet.width
                # Определяем допустимые позиции
                if w > section.width * 2 / 3:
                    allowed = [2]  # только центр
                elif w > section.width / 3:
                    allowed = [1, 3]  # только края
                else:
                    allowed = [1, 2, 3]  # любая

                # Выбираем первую свободную допустимую позицию
                assigned = None
                for pos in allowed:
                    if pos not in occupied:
                        assigned = pos
                        break
                if assigned is None:
                    # Пробуем любую свободную
                    for pos in positions:
                        if pos not in occupied:
                            assigned = pos
                            break

                if assigned is not None:
                    addr = f"{sec_id}-A{assigned}"
                    occupied.add(assigned)
                    all_ops.append(OperationSchema(
                        pallet=p_id,
                        operation="PUT",
                        newAddress=addr,
                        sequence=len(all_ops) + 1,
                    ))
                else:
                    # Не хватило адресов — возвращаем в неразмещённые
                    self.placements.pop(p_id, None)
                    self.new_pallets.append(pallet)

        # Добавляем паллеты без адреса в неразмещённые
        unaddressed = [
            p_id for p_id in self.placements
            if not any(op.pallet == p_id for op in all_ops)
        ]
        for p_id in unaddressed:
            self.placements.pop(p_id, None)

        not_placed = [
            NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
            for p in self.new_pallets
        ]

        return all_ops, not_placed

    # ------------------------------------------------------------------
    # Операции над состоянием
    # ------------------------------------------------------------------

    def _find_pallet(self, p_id: str) -> Optional[Pallet]:
        for p in self.new_pallets:
            if p.id == p_id:
                return p
        # Ищем в placements (уже размещённые паллеты могут быть нужны для address assignment)
        # Возвращаем заглушку с type_size
        return None

    def _do_place(self, pallet_id: str, section_id: str):
        """Размещает паллет и обновляет состояние секции."""
        state = self.section_states[section_id]
        # Находим паллет (может быть уже не в new_pallets после swap)
        pallet = self._find_pallet(pallet_id)
        if pallet is None:
            # Ищем среди всех изначальных new_pallets (которые могли быть в self.new_pallets раньше)
            # или создаём из placement
            return

        gap = state.section.gap_width
        state.free_width -= (pallet.width + gap)
        state.free_count -= 1
        state.placed_pallets.append(pallet)
        self.placements[pallet_id] = section_id

    def _do_remove(self, pallet_id: str, section_id: str):
        """Убирает паллет из секции (для swap)."""
        state = self.section_states[section_id]
        pallet = next((p for p in state.placed_pallets if p.id == pallet_id), None)
        if pallet is None:
            return

        gap = state.section.gap_width
        state.free_width += pallet.width + gap
        state.free_count += 1
        state.placed_pallets.remove(pallet)
        self.placements.pop(pallet_id, None)

    # ------------------------------------------------------------------
    # Построение ответа
    # ------------------------------------------------------------------

    def _build_response(
        self,
        operations: List[OperationSchema],
        not_placed: List[NotPlacedSchema],
        elapsed: float,
        placed_count: int,
    ) -> OptimizationResponse:
        total = placed_count + len(self.new_pallets)  # new_pallets = оставшиеся неразмещённые
        # На самом деле total = изначальное количество
        total = len(operations) + len(not_placed)

        return OptimizationResponse(
            optimizationId="hybrid-v2",
            mode="place",
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=(
                PlacementStatus.COMPLETE
                if len(not_placed) == 0
                else PlacementStatus.PARTIAL
            ),
            score=float(placed_count * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations,
            notPlaced=not_placed,
            metrics=MetricsSchema(
                placedPallets=placed_count,
                movedPallets=0,
                notPlacedPallets=len(not_placed),
                potentialLoss=0,
                usedSections=len(set(p.newAddress for p in operations)),
            ),
        )


# ------------------------------------------------------------------
# Входная точка: совместима с global_optimizer.run_optimization()
# ------------------------------------------------------------------

def run_hybrid_v2(request: OptimizationRequest) -> OptimizationResponse:
    """Запуск Hybrid V2 из стандартного OptimizationRequest."""
    return HybridV2Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=request.settings,
    ).solve()
