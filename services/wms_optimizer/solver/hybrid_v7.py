"""Hybrid V7: BFD + Chain-Swap + Joint CP-SAT Repack.

Цель: ≥3242/3406 паллет (95.2%) за ≤15 секунд.
"""
import time
import logging
import math
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
from models.address import Address
from models.occupancy_builder import build_warehouse_state
from models.pallet import Pallet, PalletTypeSize
from models.section import Section
from optimizer.potential import section_fits_pallet

logger = logging.getLogger(__name__)

TOTAL_TIME_BUDGET = 15.0


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


class HybridV7Solver:
    """BFD + Chain-Swap + Joint CP-SAT Repack."""

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

        self.existing_ids: set = {p.id for p in existing_pallets}
        self.existing_pallet_map: Dict[str, Pallet] = {p.id: p for p in existing_pallets}
        for p in existing_pallets:
            self.pallet_map[p.id] = p
        self.movable_existing_ids: set = {p.id for p in existing_pallets if p.movable}
        self.non_movable_ids: set = {p.id for p in existing_pallets if not p.movable}
        self.moved_existing_ids: set = set()
        self._old_address: Dict[str, str] = {
            p.id: p.current_address_id for p in existing_pallets if p.current_address_id
        }
        total_existing = len(existing_pallets)
        self._max_reslot_moves = (
            int(total_existing * settings.maxReslotPercent / 100)
            if settings.allowReslot and total_existing > 0
            else 0
        )

        self.section_states: Dict[str, _SectionState] = {}
        self._init_section_states(existing_pallets)

        self.placements: Dict[str, str] = {}

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
        for p in self.all_pallets:
            comp = []
            for state in self.section_states.values():
                if self._basic_fits(p, state.section):
                    comp.append(state.id)
            self.compatible[p.id] = comp
        for p_id in self.movable_existing_ids:
            if p_id not in self.compatible:
                p = self.existing_pallet_map[p_id]
                comp = []
                for state in self.section_states.values():
                    if self._basic_fits(p, state.section):
                        comp.append(state.id)
                self.compatible[p_id] = comp
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
        if self.settings.strictNarrowAislePlacement and section.narrow_aisle and not pallet.is_narrow:
            return False
        return True

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.all_pallets)
        logger.info(f"Hybrid V7: запуск, {total} паллет × {len(self.sections)} секций"
                    f" allowReslot={self.settings.allowReslot}")

        # Фаза 1: BFD
        n1 = self._phase_bfd_single()
        elapsed = time.time() - t0
        logger.info(f"  [BFD]         {n1}/{total} ({n1/total*100:.1f}%) за {elapsed:.1f}с")

        # Фаза 2: Chain-Swap
        t2 = time.time()
        n2 = self._phase_chain_swap()
        logger.info(f"  [Chain-Swap]  {n2}/{total} ({n2/total*100:.1f}%) за {time.time()-t2:.1f}с")

        # Фаза 3: Joint CP-SAT Repack
        t4 = time.time()
        n4 = self._phase_consolidation()
        logger.info(f"  [Repack]      {n4}/{total} ({n4/total*100:.1f}%) за {time.time()-t4:.1f}с")

        operations, not_placed = self._assign_addresses()

        elapsed = time.time() - t0
        placed_count = len([op for op in operations if op.operation == "PUT"])
        moved_count = len([op for op in operations if op.operation == "MOVE"])
        logger.info(f"Hybrid V7: итог {placed_count} placed + {moved_count} moved / {total} new за {elapsed:.1f}с")
        return self._build_response(operations, not_placed, elapsed, placed_count, moved_count)

    # ------------------------------------------------------------------
    # Фаза 1: BFD
    # ------------------------------------------------------------------

    def _phase_bfd_single(self) -> int:
        type_groups: Dict[tuple, List[Pallet]] = defaultdict(list)
        for p in self.new_pallets:
            key = (p.is_narrow, p.height, p.width, p.depth, p.weight)
            type_groups[key].append(p)

        sorted_keys = sorted(type_groups.keys(), key=lambda k: (not k[0], -k[1], -k[2], -k[4]))

        placed_ids = set()
        for key in sorted_keys:
            for pallet in type_groups[key]:
                best_id = None
                best_occ = -1
                best_rem = float("inf")
                for sec_id in self.compatible[pallet.id]:
                    state = self.section_states[sec_id]
                    if state.free_count <= 0:
                        continue
                    if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                        continue
                    occ = len(state.placed_pallets)
                    gap = state.gap_width
                    rem = state.free_width - (pallet.width + gap)
                    if occ > best_occ or (occ == best_occ and rem < best_rem):
                        best_occ = occ
                        best_rem = rem
                        best_id = sec_id
                if best_id:
                    self._do_place(pallet.id, best_id)
                    placed_ids.add(pallet.id)

        self.new_pallets = [p for p in self.new_pallets if p.id not in self.placements]
        return len(self.placements)

    # ------------------------------------------------------------------
    # Фаза 2: Chain-Swap
    # ------------------------------------------------------------------

    def _phase_chain_swap(self) -> int:
        if not self.new_pallets:
            return len(self.placements)

        improved = True
        iteration = 0
        while improved and iteration < 5:
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
        compatible = self.compatible.get(leftover.id, [])
        if not compatible:
            return None

        best_chain = None
        best_score = float("inf")

        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            if state_a.free_width >= leftover.width + state_a.gap_width:
                score = state_a.free_width - (leftover.width + state_a.gap_width)
                if score < best_score:
                    best_score = score
                    best_chain = [("place", leftover.id, sec_a_id)]

        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            gap_a = state_a.gap_width
            req = leftover.width + gap_a

            for placed_a in list(state_a.placed_pallets):
                if placed_a.id in self.non_movable_ids:
                    continue
                if placed_a.id in self.movable_existing_ids:
                    if len(self.moved_existing_ids) >= self._max_reslot_moves:
                        continue
                if state_a.free_width + placed_a.width + gap_a < req:
                    continue

                for sec_b_id in self.compatible.get(placed_a.id, []):
                    if sec_b_id == sec_a_id:
                        continue
                    state_b = self.section_states[sec_b_id]
                    if state_b.free_count <= 0:
                        continue
                    if state_b.free_width >= placed_a.width + state_b.gap_width:
                        rem_a = state_a.free_width + placed_a.width + gap_a - req
                        rem_b = state_b.free_width - (placed_a.width + state_b.gap_width)
                        score = rem_a + rem_b
                        if score < best_score:
                            best_score = score
                            best_chain = [
                                ("remove", placed_a.id, sec_a_id),
                                ("place", leftover.id, sec_a_id),
                                ("place", placed_a.id, sec_b_id),
                            ]

        return best_chain

    def _execute_chain(self, chain: List[Tuple[str, str, str]]):
        old_sections = {}
        for action, p_id, sec_id in chain:
            if action == "remove" and p_id in self.movable_existing_ids:
                old_sections[p_id] = sec_id
        for action, p_id, sec_id in chain:
            if action == "remove":
                self._do_remove(p_id, sec_id)
        for action, p_id, sec_id in chain:
            if action == "place":
                self._do_place(p_id, sec_id)
                if p_id in old_sections and sec_id != old_sections[p_id]:
                    self.moved_existing_ids.add(p_id)

    # ------------------------------------------------------------------
    # Фаза 3: Joint CP-SAT Repack
    # ------------------------------------------------------------------

    def _phase_consolidation(self) -> int:
        """Joint CP-SAT: остатки + уже размещённые паллеты в near-miss секциях."""
        if not self.new_pallets:
            return len(self.placements)

        strict_narrow = self.settings.strictNarrowAislePlacement
        t_start = time.time()

        # 1. Найти near-miss секции для остатков + секции-приёмники
        # near-miss: секция подходит по габаритам для остатка
        # приёмники: секции с 0-1 паллетой (куда можно переместить паллету из 2-паллетной)
        near_miss_sections: set = set()
        for leftover in self.new_pallets:
            for si, sec in enumerate(self.sections):
                if strict_narrow and leftover.is_narrow and not sec.narrow_aisle:
                    continue
                if leftover.height > sec.height:
                    continue
                if leftover.depth > sec.depth:
                    continue
                if leftover.width > sec.eff_max_width:
                    continue
                if leftover.depth > sec.eff_max_depth:
                    continue
                if leftover.weight > sec.max_lift_weight:
                    continue
                near_miss_sections.add(si)

        # Добавляем секции-приёмники: секции с 0-1 паллетой (куда можно переместить)
        for si, sec in enumerate(self.sections):
            state = self.section_states[sec.id]
            if len(state.placed_pallets) <= 1 and state.free_count > 0:
                near_miss_sections.add(si)

        if not near_miss_sections:
            logger.info(f"    Joint CP-SAT: no sections, skip")
            return len(self.placements)

        # Ограничиваем размер: не более 300 секций
        if len(near_miss_sections) > 300:
            near_miss_sections = set(sorted(near_miss_sections,
                key=lambda si: len(self.section_states[self.sections[si].id].placed_pallets))[:300])

        logger.info(f"    Joint CP-SAT: секций={len(near_miss_sections)}")

        # 2. Движимый пул: виртуально размещённые паллеты в near-miss секциях
        sec_id_to_si = {sec.id: si for si, sec in enumerate(self.sections)}
        movable_pool: List[Pallet] = []
        for si in near_miss_sections:
            sec = self.sections[si]
            state = self.section_states[sec.id]
            for p in state.placed_pallets:
                if p.id in self.existing_ids:
                    continue
                movable_pool.append(p)

        logger.info(f"    Joint CP-SAT: движимый пул={len(movable_pool)}, остатков={len(self.new_pallets)}")

        if not movable_pool:
            logger.info(f"    Joint CP-SAT: no movable pool, skip")
            return len(self.placements)

        # 3. CP-SAT модель
        model = cp_model.CpModel()
        SCALE = 10
        all_pallets = self.new_pallets + movable_pool

        X: Dict[Tuple[str, int], cp_model.IntVar] = {}
        feasible: Dict[str, List[int]] = {}

        for p in self.new_pallets:
            feas = [si for si in near_miss_sections if self._basic_fits(p, self.sections[si])]
            feasible[p.id] = feas
            for si in feas:
                X[(p.id, si)] = model.NewBoolVar(f"lo_{p.id}_{si}")

        for p in movable_pool:
            cur_si = sec_id_to_si[self.placements[p.id]]
            feas_set = {si for si in near_miss_sections if self._basic_fits(p, self.sections[si])}
            feas_set.add(cur_si)
            feas = sorted(feas_set)
            feasible[p.id] = feas
            for si in feas:
                X[(p.id, si)] = model.NewBoolVar(f"mv_{p.id}_{si}")

        for p in self.new_pallets:
            vars_p = [X[(p.id, si)] for si in feasible[p.id]]
            if vars_p:
                model.Add(sum(vars_p) <= 1)

        for p in movable_pool:
            vars_p = [X[(p.id, si)] for si in feasible[p.id]]
            model.Add(sum(vars_p) == 1)

        for si in near_miss_sections:
            sec = self.sections[si]
            state = self.section_states[sec.id]
            vars_in_sec = [(p, X[(p.id, si)]) for p in all_pallets if (p.id, si) in X]
            if not vars_in_sec:
                continue

            fixed = [p for p in state.placed_pallets if p.id in self.existing_ids]
            n0 = len(fixed)
            width0 = sum(pp.width for pp in fixed)
            weight0 = sum(pp.weight for pp in fixed)

            count_var = sum(xv for _, xv in vars_in_sec)
            model.Add(count_var <= sec.max_pallets - n0)

            gap = int(round(sec.gap_width * SCALE))
            width_var = sum(int(round(p.width * SCALE)) * xv for p, xv in vars_in_sec)
            remaining_width = sec.width - width0 - (n0 + 1) * sec.gap_width
            model.Add(width_var + count_var * gap <= int(round(remaining_width * SCALE)))

            if not math.isinf(sec.max_weight):
                weight_var = sum(int(round(p.weight * SCALE)) * xv for p, xv in vars_in_sec)
                remaining_weight = sec.max_weight - weight0
                model.Add(weight_var <= int(round(remaining_weight * SCALE)))

        placed_sum = sum(X[(p.id, si)] for p in self.new_pallets for si in feasible[p.id])
        model.Maximize(placed_sum)

        # Warm-start
        for p in movable_pool:
            cur_si = sec_id_to_si[self.placements[p.id]]
            for si in feasible[p.id]:
                model.AddHint(X[(p.id, si)], 1 if si == cur_si else 0)
        for p in self.new_pallets:
            for si in feasible[p.id]:
                model.AddHint(X[(p.id, si)], 0)

        # 4. Решаем
        time_limit = max(2.0, TOTAL_TIME_BUDGET - (time.time() - t_start) - 2.0)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = 8

        status = solver.Solve(model)
        elapsed = time.time() - t_start

        placed = 0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            new_positions: Dict[str, int] = {}
            for p in all_pallets:
                for si in feasible[p.id]:
                    if solver.Value(X[(p.id, si)]) == 1:
                        new_positions[p.id] = si
                        break

            # Очищаем near-miss секции от движимых
            movable_ids = {p.id for p in movable_pool}
            for si in near_miss_sections:
                sec = self.sections[si]
                state = self.section_states[sec.id]
                state.placed_pallets = [p for p in state.placed_pallets if p.id not in movable_ids]
                used = sum(p.width + sec.gap_width for p in state.placed_pallets)
                state.free_width = sec.width - used - sec.gap_width
                state.free_count = sec.max_pallets - len(state.placed_pallets)

            # Размещаем движимые
            for p in movable_pool:
                si = new_positions.get(p.id)
                if si is not None:
                    self._do_place(p.id, self.sections[si].id)

            # Размещаем остатки
            for p in self.new_pallets:
                si = new_positions.get(p.id)
                if si is not None:
                    self._do_place(p.id, self.sections[si].id)
                    placed += 1

        logger.info(f"    Joint CP-SAT: status={solver.StatusName(status)} placed={placed} time={elapsed:.1f}s")

        if placed > 0:
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
        pallet = self.pallet_map.get(pallet_id) or self.existing_pallet_map.get(pallet_id)
        if pallet is None:
            return
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
        for p_id in self.existing_ids:
            if p_id not in self.moved_existing_ids:
                p = self.existing_pallet_map.get(p_id)
                if p and p.current_section_id:
                    by_section[p.current_section_id].append(p_id)

        for sec_id, p_ids in by_section.items():
            section = self.section_states[sec_id].section
            real_addresses = self._sec_addresses.get(sec_id, ["", "", ""])

            occupied: set = set()
            occupied_widths: Dict[int, float] = {}
            for p_id in list(p_ids):
                if p_id in self.existing_ids and p_id not in self.moved_existing_ids:
                    old_addr = self._old_address.get(p_id, "")
                    if old_addr and old_addr in real_addresses:
                        idx = real_addresses.index(old_addr)
                        p = self.pallet_map.get(p_id)
                        if p:
                            occupied.add(idx)
                            occupied_widths[idx] = p.width
                            if idx in (0, 2) and p.width > section.width / 3:
                                occupied.add(1)
                                occupied_widths[1] = p.width

            sorted_ids = sorted(
                p_ids,
                key=lambda pid: (
                    0 if pid in self.moved_existing_ids else
                    1 if pid in self.existing_ids else 2,
                    -(self.pallet_map[pid].width if pid in self.pallet_map else 0),
                ),
            )

            for p_id in sorted_ids:
                if p_id in self.existing_ids and p_id not in self.moved_existing_ids:
                    continue
                p = self.pallet_map.get(p_id)
                if p is None:
                    continue
                w = p.width
                W = section.width
                if w > W * 2 / 3:
                    allowed = [2]
                elif w > W / 3:
                    allowed = [1, 3]
                else:
                    allowed = [1, 2, 3]

                assigned = None
                for pos in allowed:
                    idx = pos - 1
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
                    if assigned in (0, 2) and w > W / 3:
                        occupied.add(1)
                        occupied_widths[1] = w
                    new_addr = real_addresses[assigned]
                    old_addr = self._old_address.get(p_id) if p_id in self.existing_ids else None
                    all_ops.append(OperationSchema(
                        pallet=p_id,
                        operation="MOVE" if (p_id in self.moved_existing_ids and old_addr) else "PUT",
                        oldAddress=old_addr if p_id in self.moved_existing_ids else None,
                        newAddress=new_addr,
                        sequence=len(all_ops) + 1,
                    ))
                else:
                    self.placements.pop(p_id, None)

        addressed_ids = {op.pallet for op in all_ops}
        for p_id in list(self.placements.keys()):
            if p_id not in addressed_ids:
                self.placements.pop(p_id, None)

        not_placed = [
            NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
            for p in self.new_pallets
        ]
        return all_ops, not_placed

    def _build_response(
        self, operations, not_placed, elapsed, placed_count, moved_count=0,
    ) -> OptimizationResponse:
        return OptimizationResponse(
            optimizationId="hybrid-v7",
            mode="place",
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.COMPLETE if not not_placed else PlacementStatus.PARTIAL,
            score=float(placed_count * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations,
            notPlaced=not_placed,
            metrics=MetricsSchema(
                placedPallets=placed_count,
                movedPallets=moved_count,
                notPlacedPallets=len(not_placed),
                potentialLoss=0,
                usedSections=len(set(op.newAddress for op in operations)),
            ),
        )


def run_hybrid_v7(request: OptimizationRequest) -> OptimizationResponse:
    return HybridV7Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=request.settings,
    ).solve()
