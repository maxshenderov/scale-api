"""Hybrid V3: BFD + Chain-Swap Local Search + Micro CP-SAT.

Адаптирован под реальные схемы проекта (api.schemas, models.*, optimizer.potential).
Ключевая инновация: цепочки перемещений глубины 2 с атомарным выполнением.
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
    section: Section
    free_width: float
    free_count: int
    placed_pallets: List[Pallet] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.section.id

    @property
    def gap_width(self) -> float:
        return self.section.gap_width


class HybridV3Solver:
    """BFD + Chain-Swap (глубина 2) + Micro CP-SAT."""

    def __init__(
        self,
        occupancy: List[OccupancySectionSchema],
        new_pallets: List[NewPalletSchema],
        settings: OptimizationSettingsSchema,
    ):
        self.settings = settings
        self.time_limit = settings.timeLimitSeconds

        sections, addresses, existing_pallets = build_warehouse_state(occupancy)
        self.sections: List[Section] = sections

        # Карта section_id → [address1, address2, address3] из occupancy
        self._sec_addresses: Dict[str, List[str]] = {}
        for row in occupancy:
            self._sec_addresses[row.section_id] = [
                row.address1, row.address2, row.address3
            ]

        self.all_pallets: List[Pallet] = [
            Pallet(
                id=p.id,
                type_size=PalletTypeSize(
                    width=p.width, height=p.height, depth=p.depth, weight=p.weight
                ),
            )
            for p in new_pallets
        ]
        self.pallet_map: Dict[str, Pallet] = {p.id: p for p in self.all_pallets}
        self.new_pallets: List[Pallet] = list(self.all_pallets)

        self.section_states: Dict[str, _SectionState] = {}
        self._init_section_states(existing_pallets)

        self.placements: Dict[str, str] = {}  # pallet_id → section_id
        self.existing_ids: set = {p.id for p in existing_pallets}

        # Кэш совместимости
        self.compatible: Dict[str, List[str]] = {}
        self._precompute_compatibility()

    def _init_section_states(self, existing: List[Pallet]):
        by_section: Dict[str, List[Pallet]] = defaultdict(list)
        for p in existing:
            if p.current_section_id:
                by_section[p.current_section_id].append(p)

        for sec in self.sections:
            in_sec = by_section.get(sec.id, [])
            used = sum(p.width + sec.gap_width for p in in_sec)
            self.section_states[sec.id] = _SectionState(
                section=sec,
                free_width=sec.width - used - sec.gap_width,
                free_count=sec.max_pallets - len(in_sec),
                placed_pallets=list(in_sec),
            )

    def _precompute_compatibility(self):
        """Кэш: для каждой паллеты — все секции, куда она физически влезает."""
        for p in self.all_pallets:
            comp = []
            for state in self.section_states.values():
                if self._basic_fits(p, state.section):
                    comp.append(state.id)
            self.compatible[p.id] = comp
        logger.info(f"  Совместимость: предвычислено для {len(self.all_pallets)} паллет")

    def _basic_fits(self, pallet: Pallet, section: Section) -> bool:
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
        if pallet.weight > section.max_lift_weight:
            return False
        if section.narrow_aisle and not pallet.is_narrow:
            return False
        return True

    # ------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.all_pallets)
        logger.info(f"Hybrid V3: запуск, {total} паллет × {len(self.sections)} секций")

        # Фаза 1: BFD
        n1 = self._phase_bfd()
        print(f"  [BFD]         {n1}/{total} ({n1/total*100:.1f}%)")

        # Фаза 2: Chain-Swap
        n2 = self._phase_chain_swap()
        print(f"  [Chain-Swap]  {n2}/{total} ({n2/total*100:.1f}%)")

        # Фаза 3: Micro CP-SAT
        n3 = self._phase_micro_cpsat()
        print(f"  [CP-SAT]      {n3}/{total} ({n3/total*100:.1f}%)")

        # Фаза 4: Адреса
        operations, not_placed = self._assign_addresses()

        elapsed = time.time() - t0
        logger.info(f"Hybrid V3: итог {n3}/{total} за {elapsed:.1f}с")
        return self._build_response(operations, not_placed, elapsed, n3)

    # ------------------------------------------------------------------
    # Фаза 1: BFD
    # ------------------------------------------------------------------

    def _phase_bfd(self) -> int:
        """Best-Fit Decreasing с группировкой по типоразмерам."""
        type_groups: Dict[tuple, List[Pallet]] = defaultdict(list)
        for p in self.new_pallets:
            key = (p.is_narrow, p.height, p.width, p.depth, p.weight)
            type_groups[key].append(p)

        sorted_keys = sorted(type_groups.keys(), key=lambda k: (not k[0], -k[1], -k[2], -k[4]))

        placed_ids = set()
        for key in sorted_keys:
            for pallet in type_groups[key]:
                best_id = None
                min_rem = float("inf")
                for sec_id in self.compatible[pallet.id]:
                    state = self.section_states[sec_id]
                    if state.free_count <= 0:
                        continue
                    if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                        continue
                    gap = state.gap_width
                    rem = state.free_width - (pallet.width + gap)
                    if rem < min_rem:
                        min_rem = rem
                        best_id = sec_id
                if best_id:
                    self._do_place(pallet.id, best_id)
                    placed_ids.add(pallet.id)

        self.new_pallets = [p for p in self.new_pallets if p.id not in placed_ids]
        return len(placed_ids)

    # ------------------------------------------------------------------
    # Фаза 2: Chain-Swap (глубина 2)
    # ------------------------------------------------------------------

    def _phase_chain_swap(self) -> int:
        """Итеративный chain-swap: для каждого неразмещённого паллета ищет цепочку."""
        if not self.new_pallets:
            return len(self.placements)

        improved = True
        iteration = 0
        while improved and iteration < 5:  # до 5 итераций
            improved = False
            iteration += 1
            t_iter = time.time()
            leftovers = sorted(self.new_pallets, key=lambda p: (-p.width, -p.height))
            self.new_pallets = []

            swaps_this_round = 0
            for leftover in leftovers:
                chain = self._find_chain(leftover)
                if chain:
                    self._execute_chain(chain)
                    improved = True
                    swaps_this_round += 1
                else:
                    self.new_pallets.append(leftover)

            if swaps_this_round:
                logger.info(f"  Chain-Swap iter {iteration}: {swaps_this_round} цепочек за {time.time() - t_iter:.1f}с")
            else:
                logger.info(f"  Chain-Swap iter {iteration}: нет цепочек, выход")

        return len(self.placements)

    def _find_chain(self, leftover: Pallet) -> Optional[List[Tuple[str, str, str]]]:
        """Ищет цепочку для размещения leftover.
        Сначала быстрая проверка (глубина 0), потом глубина 1, потом 2.
        """
        compatible = self.compatible.get(leftover.id, [])
        if not compatible:
            return None

        gap_sample = self.section_states[compatible[0]].gap_width

        # --- Уровень 0: влезает сразу ---
        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            if state_a.free_width >= leftover.width + state_a.gap_width:
                return [("place", leftover.id, sec_a_id)]

        # --- Уровень 1: вытесняем 1 паллет из A, размещаем её в B ---
        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            gap_a = state_a.gap_width
            req = leftover.width + gap_a

            for placed_a in list(state_a.placed_pallets):
                if placed_a.id in self.existing_ids:
                    continue
                if state_a.free_width + placed_a.width + gap_a < req:
                    continue

                # Куда пристроить вытесненный?
                for sec_b_id in self.compatible.get(placed_a.id, []):
                    if sec_b_id == sec_a_id:
                        continue
                    state_b = self.section_states[sec_b_id]
                    if state_b.free_count <= 0:
                        continue
                    if state_b.free_width >= placed_a.width + state_b.gap_width:
                        return [
                            ("remove", placed_a.id, sec_a_id),
                            ("place", leftover.id, sec_a_id),
                            ("place", placed_a.id, sec_b_id),
                        ]

        # --- Уровень 2: вытесняем 2 паллета (дорогой, ограничиваем) ---
        MAX_LEVEL2_CHECKS = 2000  # ограничение на поиск глубины 2
        checks = 0
        for sec_a_id in compatible[:20]:  # только первые 20 совместимых секций
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            gap_a = state_a.gap_width
            req = leftover.width + gap_a

            for placed_a in list(state_a.placed_pallets):
                if placed_a.id in self.existing_ids:
                    continue
                if state_a.free_width + placed_a.width + gap_a < req:
                    continue

                for sec_b_id in self.compatible.get(placed_a.id, [])[:20]:
                    if sec_b_id == sec_a_id:
                        continue
                    state_b = self.section_states[sec_b_id]
                    if state_b.free_count <= 0:
                        continue
                    gap_b = state_b.gap_width
                    req_b = placed_a.width + gap_b

                    # Влезает сразу в B
                    if state_b.free_width >= req_b:
                        return [
                            ("remove", placed_a.id, sec_a_id),
                            ("place", leftover.id, sec_a_id),
                            ("place", placed_a.id, sec_b_id),
                        ]

                    # Пробуем вытеснить из B
                    for placed_b in list(state_b.placed_pallets):
                        if placed_b.id in self.existing_ids:
                            continue
                        if state_b.free_width + placed_b.width + gap_b < req_b:
                            continue

                        for sec_c_id in self.compatible.get(placed_b.id, [])[:20]:
                            checks += 1
                            if checks > MAX_LEVEL2_CHECKS:
                                return None
                            if sec_c_id in (sec_a_id, sec_b_id):
                                continue
                            state_c = self.section_states[sec_c_id]
                            if state_c.free_count <= 0:
                                continue
                            if state_c.free_width >= placed_b.width + state_c.gap_width:
                                return [
                                    ("remove", placed_a.id, sec_a_id),
                                    ("remove", placed_b.id, sec_b_id),
                                    ("place", leftover.id, sec_a_id),
                                    ("place", placed_a.id, sec_b_id),
                                    ("place", placed_b.id, sec_c_id),
                                ]

        return None

    def _removable_width(self, state: _SectionState, skip_existing: bool = True) -> float:
        """Суммарная ширина паллет, которые можно вытеснить из секции."""
        total = 0.0
        for p in state.placed_pallets:
            if skip_existing and p.id in self.existing_ids:
                continue
            total += p.width + state.gap_width
        return total

    def _execute_chain(self, chain: List[Tuple[str, str, str]]):
        """Атомарное выполнение цепочки: сначала remove, потом place."""
        for action, p_id, sec_id in chain:
            if action == "remove":
                self._do_remove(p_id, sec_id)
        for action, p_id, sec_id in chain:
            if action == "place":
                self._do_place(p_id, sec_id)

    # ------------------------------------------------------------------
    # Фаза 3: Micro CP-SAT
    # ------------------------------------------------------------------

    def _phase_micro_cpsat(self) -> int:
        if not self.new_pallets or len(self.new_pallets) > 200:
            return len(self.placements)

        model = cp_model.CpModel()
        SCALE = 10
        states = list(self.section_states.values())

        x = {}
        for p in self.new_pallets:
            for state in states:
                if state.free_count > 0 and state.id in self.compatible.get(p.id, []):
                    x[(p.id, state.id)] = model.NewBoolVar(f"x_{p.id}_{state.id}")

        for p in self.new_pallets:
            valid = [x[(p.id, s.id)] for s in states if (p.id, s.id) in x]
            if valid:
                model.Add(sum(valid) <= 1)

        for state in states:
            vars_in = [x[(p.id, state.id)] for p in self.new_pallets if (p.id, state.id) in x]
            if not vars_in:
                continue
            model.Add(sum(vars_in) <= state.free_count)
            width_expr = sum(
                int(p.width * SCALE) * x[(p.id, state.id)]
                for p in self.new_pallets if (p.id, state.id) in x
            )
            model.Add(width_expr <= int(state.free_width * SCALE))

        model.Maximize(sum(x.values()))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10.0
        solver.parameters.num_search_workers = 4

        status = solver.Solve(model)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    self._do_place(p_id, sec_id)

        self.new_pallets = [p for p in self.new_pallets if p.id not in self.placements]
        return len(self.placements)

    # ------------------------------------------------------------------
    # Операции
    # ------------------------------------------------------------------

    def _do_place(self, pallet_id: str, section_id: str):
        state = self.section_states[section_id]
        pallet = self.pallet_map[pallet_id]
        gap = state.gap_width
        state.free_width -= (pallet.width + gap)
        state.free_count -= 1
        state.placed_pallets.append(pallet)
        self.placements[pallet_id] = section_id

    def _do_remove(self, pallet_id: str, section_id: str):
        state = self.section_states[section_id]
        pallet = self.pallet_map[pallet_id]
        gap = state.gap_width
        state.free_width += pallet.width + gap
        state.free_count += 1
        state.placed_pallets = [p for p in state.placed_pallets if p.id != pallet_id]
        self.placements.pop(pallet_id, None)

    # ------------------------------------------------------------------
    # Адреса
    # ------------------------------------------------------------------

    def _assign_addresses(self) -> Tuple[List[OperationSchema], List[NotPlacedSchema]]:
        all_ops: List[OperationSchema] = []
        by_section: Dict[str, List[str]] = defaultdict(list)
        for p_id, sec_id in self.placements.items():
            by_section[sec_id].append(p_id)

        for sec_id, p_ids in by_section.items():
            section = self.section_states[sec_id].section
            # Реальные GUID-адреса из occupancy
            real_addresses = self._sec_addresses.get(sec_id, ["", "", ""])

            sorted_ids = sorted(
                p_ids,
                key=lambda pid: self.pallet_map[pid].width,
                reverse=True,
            )

            # Определяем занятые позиции (существующие паллеты)
            occupied = set()
            occupied_widths = {}  # idx → ширина паллеты
            existing_in_sec = [p for p in self.section_states[sec_id].placed_pallets
                              if p.id in self.existing_ids]
            # Не отмечаем занятые — они уже в state.placed_pallets

            for p_id in sorted_ids:
                p = self.pallet_map[p_id]
                w = p.width
                W = section.width
                # Позиции 1-based: 1=левый край, 2=центр, 3=правый край
                if w > W * 2 / 3:
                    allowed = [2]  # только центр
                elif w > W / 3:
                    allowed = [1, 3]  # только края
                else:
                    allowed = [1, 2, 3]  # любая

                assigned = None
                for pos in allowed:
                    idx = pos - 1
                    # Addr2 блокирован только если Addr1/Addr3 заняты ШИРОКОЙ (>W/3) паллетой
                    if idx == 1:
                        if 0 in occupied and occupied_widths.get(0, 0) > W / 3:
                            continue
                        if 2 in occupied and occupied_widths.get(2, 0) > W / 3:
                            continue
                    if idx not in occupied and real_addresses[idx]:
                        assigned = idx
                        break
                if assigned is None:
                    for idx in range(3):
                        if idx == 1:
                            if 0 in occupied and occupied_widths.get(0, 0) > W / 3:
                                continue
                            if 2 in occupied and occupied_widths.get(2, 0) > W / 3:
                                continue
                        if idx not in occupied and real_addresses[idx]:
                            assigned = idx
                            break

                if assigned is not None:
                    occupied.add(assigned)
                    occupied_widths[assigned] = w
                    # Если паллета на Addr1/Addr3 широкая (>W/3) — блокируем Addr2
                    if assigned in (0, 2) and w > W / 3:
                        occupied.add(1)
                        occupied_widths[1] = w  # фиктивная ширина для блокировки

                if assigned is not None:
                    occupied.add(assigned)
                    all_ops.append(OperationSchema(
                        pallet=p_id,
                        operation="PUT",
                        newAddress=real_addresses[assigned],
                        sequence=len(all_ops) + 1,
                    ))
                else:
                    self.placements.pop(p_id, None)

        # Паллеты без адреса → неразмещённые
        addressed_ids = {op.pallet for op in all_ops}
        for p_id in list(self.placements.keys()):
            if p_id not in addressed_ids:
                self.placements.pop(p_id, None)

        not_placed = [
            NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
            for p in self.new_pallets
        ]
        return all_ops, not_placed

    # ------------------------------------------------------------------
    # Ответ
    # ------------------------------------------------------------------

    def _build_response(
        self, operations, not_placed, elapsed, placed_count
    ) -> OptimizationResponse:
        return OptimizationResponse(
            optimizationId="hybrid-v3",
            mode="place",
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.COMPLETE if not not_placed else PlacementStatus.PARTIAL,
            score=float(placed_count * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations,
            notPlaced=not_placed,
            metrics=MetricsSchema(
                placedPallets=placed_count,
                movedPallets=0,
                notPlacedPallets=len(not_placed),
                potentialLoss=0,
                usedSections=len(set(op.newAddress for op in operations)),
            ),
        )


def run_hybrid_v3(request: OptimizationRequest) -> OptimizationResponse:
    return HybridV3Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=request.settings,
    ).solve()
