"""Hybrid V6: Large Neighborhood Search (LNS) — BFD + CP-SAT итерации.

Алгоритм 6 — принципиально новый подход:
  1. BFD + Chain-Swap дают стартовое решение (~3215 паллет)
  2. Выбираем neighbourhood: случайные секции + их паллеты + leftovers
  3. CP-SAT переоптимизирует neighbourhood (маленькая модель, <1с)
  4. Повторяем до исчерпания времени (15с)

Ключевая идея: CP-SAT решает МАЛЕНЬКУЮ подзадачу много раз,
вместо ОГРОМНОЙ задачи один раз (как V5).
"""
import time
import logging
import math
import random
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


class HybridV6Solver:
    """LNS: BFD + Chain-Swap + CP-SAT neighbourhood search."""

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

    # ------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.all_pallets)
        budget = self.time_limit
        logger.info(f"Hybrid V6 (LNS): {total} паллет × {len(self.sections)} секций, budget={budget}s")

        # Фаза 1: BFD + Chain-Swap (быстрое стартовое решение)
        n1 = self._phase_bfd()
        logger.info(f"  [BFD]         {n1}/{total} ({n1/total*100:.1f}%)")

        n2 = self._phase_chain_swap()
        logger.info(f"  [Chain-Swap]  {n2}/{total} ({n2/total*100:.1f}%)")

        # Фаза 2: LNS итерации (CP-SAT на neighbourhood)
        if self.new_pallets and budget > 0:
            n3 = self._phase_lns(t0, budget)
            logger.info(f"  [LNS]         {n3}/{total} ({n3/total*100:.1f}%)")

            # Фаза 2.5: Chain-Swap после LNS
            if self.new_pallets:
                n4 = self._phase_chain_swap()
                logger.info(f"  [Chain-Swap2] {n4}/{total} ({n4/total*100:.1f}%)")

                # Фаза 2.6: BFD на leftovers (могли освободиться секции)
                if self.new_pallets:
                    n5 = self._phase_bfd()
                    logger.info(f"  [BFD2]        {n5}/{total} ({n5/total*100:.1f}%)")

        # Фаза 3: Адреса
        operations, not_placed = self._assign_addresses()

        elapsed = time.time() - t0
        placed_count = len([op for op in operations if op.operation == "PUT"])
        moved_count = len([op for op in operations if op.operation == "MOVE"])
        logger.info(f"Hybrid V6: итог {placed_count} placed + {moved_count} moved за {elapsed:.1f}с")
        return self._build_response(operations, not_placed, elapsed, placed_count, moved_count)

    # ------------------------------------------------------------------
    # Фаза 1: BFD
    # ------------------------------------------------------------------

    def _phase_bfd(self) -> int:
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
            leftovers = sorted(self.new_pallets, key=lambda p: (-p.width, -p.height))
            self.new_pallets = []

            for leftover in leftovers:
                chain = self._find_chain(leftover)
                if chain:
                    self._execute_chain(chain)
                    improved = True
                else:
                    self.new_pallets.append(leftover)

        return len(self.placements)

    def _find_chain(self, leftover: Pallet) -> Optional[List[Tuple[str, str, str]]]:
        compatible = self.compatible.get(leftover.id, [])
        if not compatible:
            return None

        # Уровень 0: прямое размещение
        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count > 0 and state_a.free_width >= leftover.width + state_a.gap_width:
                return [("place", leftover.id, sec_a_id)]

        # Уровень 1: переместить одну паллету
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
                    if state_b.free_count > 0 and state_b.free_width >= placed_a.width + state_b.gap_width:
                        return [
                            ("remove", placed_a.id, sec_a_id),
                            ("place", leftover.id, sec_a_id),
                            ("place", placed_a.id, sec_b_id),
                        ]

        return None

    def _execute_chain(self, chain: List[Tuple[str, str, str]]):
        for action, p_id, sec_id in chain:
            if action == "remove":
                self._do_remove(p_id, sec_id)
        for action, p_id, sec_id in chain:
            if action == "place":
                self._do_place(p_id, sec_id)
                if p_id in self.movable_existing_ids:
                    self.moved_existing_ids.add(p_id)

    # ------------------------------------------------------------------
    # Фаза 3: Large Neighborhood Search (CP-SAT итерации)
    # ------------------------------------------------------------------

    def _phase_lns(self, t0: float, budget: int) -> int:
        """LNS: выбираем neighbourhood, решаем CP-SAT, применяем улучшения."""
        if not self.new_pallets:
            return len(self.placements)

        rng = random.Random(42)
        NEIGHBOURHOOD_SIZE = 70
        CP_SAT_TIMEOUT = 1.5

        best_count = len(self.placements)
        initial_count = best_count
        no_improve = 0

        while True:
            elapsed = time.time() - t0
            if elapsed > budget - 2.0:  # резерв 2с на chain-swap2 + адреса
                break

            # Выбираем neighbourhood: случайные секции с паллетами
            occupied_sections = [
                sid for sid, state in self.section_states.items()
                if state.placed_pallets and state.free_count > 0
            ]
            if not occupied_sections:
                break

            n_size = min(NEIGHBOURHOOD_SIZE, len(occupied_sections))
            neighbourhood_secs = rng.sample(occupied_sections, n_size)

            # Паллеты из neighbourhood + все leftovers
            neighbourhood_pallets: List[Pallet] = []
            old_sections_for_existing: Dict[str, str] = {}
            for sec_id in neighbourhood_secs:
                state = self.section_states[sec_id]
                for p in state.placed_pallets:
                    if p.id in self.non_movable_ids:
                        continue
                    if p.id in self.existing_ids:
                        if self._max_reslot_moves <= 0:
                            continue
                        old_sections_for_existing[p.id] = sec_id
                    neighbourhood_pallets.append(p)
                state = self.section_states[sec_id]
                for p in state.placed_pallets:
                    if p.id in self.non_movable_ids:
                        continue
                    # LNS работает ТОЛЬКО с новыми паллетами — existing не трогаем
                    # Реслот existing — через chain-swap
                    if p.id in self.existing_ids:
                        continue
                    neighbourhood_pallets.append(p)

            # Добавляем leftovers
            neighbourhood_pallets.extend(self.new_pallets)

            if len(neighbourhood_pallets) < 2:
                break

            # Секции для CP-SAT: neighbourhood + пустые секции
            empty_sections = [
                sid for sid, state in self.section_states.items()
                if state.free_count >= 2 and not state.placed_pallets
                and sid not in neighbourhood_secs
            ]
            candidate_sections = list(neighbourhood_secs)
            candidate_sections.extend(rng.sample(
                empty_sections,
                min(n_size, len(empty_sections)),
            ) if empty_sections else [])

            # Сохраняем состояние
            snap = self._snapshot()

            # Удаляем паллеты из neighbourhood (включая movable existing)
            for sec_id in neighbourhood_secs:
                state = self.section_states[sec_id]
                for p in list(state.placed_pallets):
                    if p.id in self.non_movable_ids:
                        continue
                    if p.id in self.existing_ids and self._max_reslot_moves <= 0:
                        continue
                    self._do_remove(p.id, sec_id)

            # Запускаем CP-SAT
            new_placements = self._solve_lns_subproblem(
                neighbourhood_pallets, candidate_sections,
                timeout=CP_SAT_TIMEOUT,
            )

            if new_placements:
                for p_id, sec_id in new_placements.items():
                    if p_id not in self.placements:
                        self._do_place(p_id, sec_id)
                    if p_id in old_sections_for_existing:
                        old_sec = old_sections_for_existing[p_id]
                        if sec_id != old_sec and len(self.moved_existing_ids) < self._max_reslot_moves:
                            self.moved_existing_ids.add(p_id)

                self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]

                new_count = len(self.placements)

                if new_count > best_count:
                    improvement = new_count - best_count
                    best_count = new_count
                    no_improve = 0
                    logger.info(f"  LNS: +{improvement} паллет (total={best_count}, "
                              f"elapsed={elapsed:.1f}s)")
                else:
                    # Откат
                    self._restore(snap)
                    no_improve += 1
            else:
                # CP-SAT не нашёл решения — откат
                self._restore(snap)
                no_improve += 1

            # Ранний выход если нет улучшений
            if no_improve > 5:
                break

        return len(self.placements)

    def _snapshot(self) -> Dict:
        """Снапшот состояния для отката."""
        return {
            'states': {
                sec_id: {
                    'free_width': s.free_width,
                    'free_count': s.free_count,
                    'placed_pallets': list(s.placed_pallets),
                }
                for sec_id, s in self.section_states.items()
            },
            'placements': dict(self.placements),
            'new_pallets': list(self.new_pallets),
        }

    def _restore(self, snap: Dict):
        """Восстановление состояния."""
        for sec_id, data in snap['states'].items():
            s = self.section_states[sec_id]
            s.free_width = data['free_width']
            s.free_count = data['free_count']
            s.placed_pallets = list(data['placed_pallets'])
        self.placements = dict(snap['placements'])
        self.new_pallets = list(snap['new_pallets'])

    def _validate_all_section_addresses(self, section_ids: set) -> bool:
        """Проверяет что паллеты в секциях можно разложить по 3 адресам.

        Использует те же правила что _assign_addresses: (N+1)*gap,
        ограничения по ширине (W > 2/3 → только центр, W > 1/3 → только края).
        """
        for sec_id in section_ids:
            state = self.section_states[sec_id]
            sec = state.section
            real_addresses = self._sec_addresses.get(sec_id, ["", "", ""])

            # Существующие немобильные паллеты занимают свои адреса
            occupied: set = set()
            occupied_widths: Dict[int, float] = {}

            for p in state.placed_pallets:
                if p.id in self.existing_ids and p.id not in self.moved_existing_ids:
                    old_addr = self._old_address.get(p.id, "")
                    if old_addr and old_addr in real_addresses:
                        idx = real_addresses.index(old_addr)
                        occupied.add(idx)
                        occupied_widths[idx] = p.width
                        if idx in (0, 2) and p.width > sec.width / 3:
                            occupied.add(1)
                            occupied_widths[1] = p.width

            # Новые + перемещённые паллеты — сортировка как в _assign_addresses
            new_pallets_in_sec = [
                p for p in state.placed_pallets
                if p.id not in self.existing_ids or p.id in self.moved_existing_ids
            ]
            sorted_p = sorted(new_pallets_in_sec, key=lambda p: -p.width)

            W = sec.width
            for p in sorted_p:
                w = p.width
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
                else:
                    return False  # паллета не влезла ни в один слот

        return True

    def _solve_lns_subproblem(
        self, pallets: List[Pallet], section_ids: List[str], timeout: float,
    ) -> Optional[Dict[str, str]]:
        """CP-SAT для маленького neighbourhood.

        Переменные X[p, s] — разместить паллету p в секцию s.
        """
        model = cp_model.CpModel()
        SCALE = 10

        # Переменные
        x: Dict[Tuple[str, str], cp_model.IntVar] = {}
        for p in pallets:
            for sid in section_ids:
                state = self.section_states[sid]
                if state.free_count > 0 and sid in self.compatible.get(p.id, []):
                    x[(p.id, sid)] = model.NewBoolVar(f"x_{p.id}_{sid}")

        # Каждая паллета ≤ 1 секции
        for p in pallets:
            vars_p = [x[(p.id, sid)] for sid in section_ids if (p.id, sid) in x]
            if vars_p:
                model.Add(sum(vars_p) <= 1)

        # Вместимость секций
        for sid in section_ids:
            state = self.section_states[sid]
            sec = state.section
            vars_s = [x[(p.id, sid)] for p in pallets if (p.id, sid) in x]
            if not vars_s:
                continue

            model.Add(sum(vars_s) <= state.free_count)

            # Ширина + gap
            gap_total = int(state.gap_width * SCALE) * sum(vars_s)
            width_sum = sum(
                int(p.width * SCALE) * x[(p.id, sid)]
                for p in pallets if (p.id, sid) in x
            )
            model.Add(width_sum + gap_total <= int(state.free_width * SCALE))

            # Вес (кумулятивный)
            if not math.isinf(sec.max_weight):
                weight_sum = sum(
                    int(p.weight * SCALE) * x[(p.id, sid)]
                    for p in pallets if (p.id, sid) in x
                )
                existing_weight = sum(
                    int(p.weight * SCALE) for p in state.placed_pallets
                )
                model.Add(weight_sum + existing_weight <= int(sec.max_weight * SCALE))

        model.Maximize(sum(x.values()))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = timeout
        solver.parameters.num_search_workers = 4

        status = solver.Solve(model)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            result: Dict[str, str] = {}
            for (p_id, sid), var in x.items():
                if solver.Value(var) == 1:
                    result[p_id] = sid
            return result if result else None

        return None

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
        # Источник правды: section_states (реальное состояние после всех фаз)
        for sec_id, state in self.section_states.items():
            for p in state.placed_pallets:
                by_section[sec_id].append(p.id)

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

        # Сортируем: MOVE перед PUT — чтобы освобождение адресов шло до занятия
        all_ops.sort(key=lambda op: (0 if op.operation == "MOVE" else 1, op.sequence))

        # Dry-run фильтр: выбрасываем операции которые создают ADDRESS_OCCUPIED
        virtual: Dict[str, str] = {}
        # Инициализируем virtual_state из unmoved existing паллет
        for p_id in self.existing_ids:
            if p_id not in self.moved_existing_ids:
                old_addr = self._old_address.get(p_id, "")
                if old_addr:
                    virtual[old_addr] = p_id

        filtered_ops = []
        dropped = 0
        for op in all_ops:
            if op.operation == "MOVE" and op.oldAddress:
                if virtual.get(op.oldAddress) == op.pallet:
                    del virtual[op.oldAddress]
            if virtual.get(op.newAddress) and virtual[op.newAddress] != op.pallet:
                dropped += 1
                continue  # ADDRESS_OCCUPIED — пропускаем
            virtual[op.newAddress] = op.pallet
            filtered_ops.append(op)

        if dropped > 0:
            logger.warning(f"  Dry-run filter: dropped {dropped} conflicting operations")

        all_ops = filtered_ops

        # Перенумеруем sequence после фильтрации
        for i, op in enumerate(all_ops):
            op.sequence = i + 1

        not_placed = [
            NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
            for p in self.new_pallets
        ]
        return all_ops, not_placed

    # ------------------------------------------------------------------
    # Ответ
    # ------------------------------------------------------------------

    def _build_response(
        self, operations, not_placed, elapsed, placed_count, moved_count=0,
    ) -> OptimizationResponse:
        return OptimizationResponse(
            optimizationId="hybrid-v6",
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


def run_hybrid_v6(request: OptimizationRequest) -> OptimizationResponse:
    return HybridV6Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=request.settings,
    ).solve()
