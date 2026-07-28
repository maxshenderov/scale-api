"""Hybrid V4: LNS (Large Neighborhood Search) на базе V3.

Фаза 1: V3 (BFD + Chain-Swap) для быстрого старта.
Фаза 2: Fix-and-Optimize — итеративно «размораживает» зоны вокруг неразмещённых
        паллет и запускает микро-CP-SAT на каждой зоне.
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


class HybridV4Solver:
    """LNS: BFD+ChainSwap + Fix-and-Optimize на зонах."""

    def __init__(
        self,
        occupancy: List[OccupancySectionSchema],
        new_pallets: List[NewPalletSchema],
        settings: OptimizationSettingsSchema,
    ):
        self.settings = settings
        self.time_limit = settings.timeLimitSeconds

        sections, _, existing = build_warehouse_state(occupancy)
        self.sections: List[Section] = sections
        self.existing_ids: set = {p.id for p in existing}

        self.all_pallets: List[Pallet] = [
            Pallet(id=p.id, type_size=PalletTypeSize(
                width=p.width, height=p.height, depth=p.depth, weight=p.weight))
            for p in new_pallets
        ]
        self.pallet_map: Dict[str, Pallet] = {p.id: p for p in self.all_pallets}

        # Адреса из occupancy
        self._sec_addresses: Dict[str, List[str]] = {}
        for row in occupancy:
            self._sec_addresses[row.section_id] = [row.address1, row.address2, row.address3]

        # Кэш совместимости
        self.compatible: Dict[str, List[str]] = {}
        for p in self.all_pallets:
            self.compatible[p.id] = [
                s.id for s in self.sections if self._basic_fits(p, s)
            ]

    def _basic_fits(self, p: Pallet, s: Section) -> bool:
        if p.height > s.height: return False
        if p.depth > s.depth: return False
        if p.width > s.eff_max_width: return False
        if s.eff_max_depth > 0 and p.depth > s.eff_max_depth: return False
        if p.weight > s.max_weight: return False
        if p.weight > s.max_lift_weight: return False
        if s.narrow_aisle and not p.is_narrow: return False
        return True

    # ==================================================================
    # Основной цикл
    # ==================================================================

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.all_pallets)
        logger.info(f"Hybrid V4: LNS запуск, {total} паллет")

        # Инициализация состояния секций (копия логики V3)
        placements: Dict[str, str] = {}
        section_states: Dict[str, _SectionState] = {}
        for s in self.sections:
            section_states[s.id] = _SectionState(
                section=s,
                free_width=s.width - s.gap_width,
                free_count=s.max_pallets,
            )

        # Фаза 1: BFD
        new_pallets = list(self.all_pallets)
        n1 = self._bfd(new_pallets, placements, section_states)
        leftovers = [p for p in self.all_pallets if p.id not in placements]
        print(f"  [BFD]         {n1}/{total} ({n1/total*100:.1f}%)")

        # Фаза 2: Chain-Swap
        n2 = self._chain_swap(leftovers, placements, section_states)
        leftovers = [p for p in self.all_pallets if p.id not in placements]
        print(f"  [Chain-Swap]  {len(placements)}/{total} ({len(placements)/total*100:.1f}%)")

        # Фаза 2: LNS
        t_lns = time.time()
        max_lns = min(30.0, self.time_limit * 0.3)
        iteration = 0

        while leftovers and (time.time() - t_lns) < max_lns:
            iteration += 1
            improved = self._lns_iteration(placements, leftovers, section_states)
            leftovers = [p for p in self.all_pallets if p.id not in placements]
            if improved == 0:
                break
            print(f"  [LNS iter {iteration}] +{improved}, осталось {len(leftovers)}")

        n4 = len(placements)
        print(f"  [LNS total]  {n4}/{total} ({n4/total*100:.1f}%)")

        # Адреса
        operations, not_placed = self._assign_addresses(placements, leftovers)

        elapsed = time.time() - t0
        return self._build_response(operations, not_placed, elapsed, n4)

    # ==================================================================
    # BFD + Chain-Swap (из V3)
    # ==================================================================

    def _bfd(self, new_pallets, placements, states) -> int:
        """Best-Fit Decreasing с группировкой по типоразмерам."""
        from collections import defaultdict
        type_groups = defaultdict(list)
        for p in new_pallets:
            key = (p.is_narrow, p.height, p.width, p.depth, p.weight)
            type_groups[key].append(p)
        sorted_keys = sorted(type_groups.keys(), key=lambda k: (not k[0], -k[1], -k[2], -k[4]))

        for key in sorted_keys:
            for pallet in type_groups[key]:
                best_id, min_rem = None, float("inf")
                for sec_id in self.compatible[pallet.id]:
                    st = states[sec_id]
                    if st.free_count <= 0:
                        continue
                    if not section_fits_pallet(st.section, st.placed_pallets, pallet):
                        continue
                    rem = st.free_width - (pallet.width + st.gap_width)
                    if rem < min_rem:
                        min_rem, best_id = rem, sec_id
                if best_id:
                    self._do_place(pallet.id, best_id, placements, states)
        return len(placements)

    def _chain_swap(self, leftovers, placements, states) -> int:
        """Цепочки перемещений глубины 2."""
        improved = True
        iteration = 0
        while improved and iteration < 5:
            improved = False; iteration += 1
            leftovers = sorted(leftovers, key=lambda p: (-p.width, -p.height))
            next_leftovers = []
            for p in leftovers:
                chain = self._find_chain(p, placements, states)
                if chain:
                    self._exec_chain(chain, placements, states)
                    improved = True
                else:
                    next_leftovers.append(p)
            leftovers = next_leftovers
        return len(placements)

    def _find_chain(self, leftover, placements, states):
        compatible = self.compatible.get(leftover.id, [])
        if not compatible:
            return None

        # Уровень 0: влезает сразу
        for sec_a in compatible:
            st = states[sec_a]
            if st.free_count > 0 and st.free_width >= leftover.width + st.gap_width:
                return [("place", leftover.id, sec_a)]

        # Уровень 1: вытесняем 1 паллет
        for sec_a in compatible:
            st = states[sec_a]
            if st.free_count <= 0:
                continue
            gap = st.gap_width
            req = leftover.width + gap
            for placed in list(st.placed_pallets):
                if placed.id in self.existing_ids:
                    continue
                if st.free_width + placed.width + gap < req:
                    continue
                for sec_b in self.compatible.get(placed.id, []):
                    if sec_b == sec_a:
                        continue
                    st_b = states[sec_b]
                    if st_b.free_count > 0 and st_b.free_width >= placed.width + st_b.gap_width:
                        return [
                            ("remove", placed.id, sec_a),
                            ("place", leftover.id, sec_a),
                            ("place", placed.id, sec_b),
                        ]

        return None

    def _exec_chain(self, chain, placements, states):
        for action, p_id, sec_id in chain:
            if action == "remove":
                self._do_remove(p_id, sec_id, placements, states)
        for action, p_id, sec_id in chain:
            if action == "place":
                self._do_place(p_id, sec_id, placements, states)

    def _do_place(self, p_id, sec_id, placements, states):
        st = states[sec_id]
        p = self.pallet_map[p_id]
        st.free_width -= (p.width + st.gap_width)
        st.free_count -= 1
        st.placed_pallets.append(p)
        placements[p_id] = sec_id

    def _do_remove(self, p_id, sec_id, placements, states):
        st = states[sec_id]
        p = self.pallet_map[p_id]
        st.free_width += p.width + st.gap_width
        st.free_count += 1
        st.placed_pallets = [x for x in st.placed_pallets if x.id != p_id]
        placements.pop(p_id, None)

    def _lns_iteration(
        self, placements: Dict[str, str], leftovers: List[Pallet],
        section_states: Dict,
    ) -> int:
        # Зона влияния: секции, куда могут встать leftovers
        target_sections: set = set()
        for p in leftovers:
            target_sections.update(self.compatible[p.id])

        # Размораживаем паллеты в целевых секциях
        unfrozen_ids = {p.id for p in leftovers}
        for p_id, sec_id in list(placements.items()):
            if sec_id in target_sections and p_id not in self.existing_ids:
                unfrozen_ids.add(p_id)

        # Замороженные (не трогаем)
        frozen = {p_id: sec_id for p_id, sec_id in placements.items()
                  if p_id not in unfrozen_ids}

        # Остаточная ёмкость секций с учётом замороженных
        sec_free_w: Dict[str, float] = {}
        sec_free_n: Dict[str, int] = {}
        for s in self.sections:
            used_w = sum(self.pallet_map[pid].width + s.gap_width
                        for pid, sid in frozen.items() if sid == s.id)
            used_n = sum(1 for pid, sid in frozen.items() if sid == s.id)
            sec_free_w[s.id] = s.width - used_w - s.gap_width  # +1 gap для нового
            sec_free_n[s.id] = s.max_pallets - used_n

        # Микро CP-SAT на зоне
        SCALE = 10
        model = cp_model.CpModel()
        unfrozen_pallets = [self.pallet_map[pid] for pid in unfrozen_ids]
        leftover_ids = {p.id for p in leftovers}

        x = {}
        for p in unfrozen_pallets:
            for sec_id in self.compatible[p.id]:
                if sec_id in target_sections:
                    x[(p.id, sec_id)] = model.NewBoolVar(f"x_{p.id}_{sec_id}")

        for p in unfrozen_pallets:
            valid = [x[(p.id, s)] for s in target_sections if (p.id, s) in x]
            if valid:
                model.Add(sum(valid) <= 1)

        for sec_id in target_sections:
            vars_in = [x[(p.id, sec_id)] for p in unfrozen_pallets if (p.id, sec_id) in x]
            if not vars_in:
                continue
            model.Add(sum(vars_in) <= sec_free_n[sec_id])
            w_expr = sum(int(p.width * SCALE) * x[(p.id, sec_id)]
                        for p in unfrozen_pallets if (p.id, sec_id) in x)
            model.Add(w_expr <= int(sec_free_w[sec_id] * SCALE))

        # Цель: максимизировать размещение leftovers
        obj = [x[(p.id, s)] for p in unfrozen_pallets if p.id in leftover_ids
               for s in target_sections if (p.id, s) in x]
        model.Maximize(sum(obj))

        # Warm start от текущего размещения
        for p in unfrozen_pallets:
            if p.id in placements:
                sec_id = placements[p.id]
                if (p.id, sec_id) in x:
                    model.AddHint(x[(p.id, sec_id)], 1)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0
        solver.parameters.num_search_workers = 4

        status = solver.Solve(model)
        improved = 0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Удаляем старые размещения размороженных
            for p_id in list(unfrozen_ids):
                placements.pop(p_id, None)
            # Применяем новые
            for (p_id, sec_id), var in x.items():
                if solver.Value(var) == 1:
                    placements[p_id] = sec_id
                    if p_id in leftover_ids:
                        improved += 1

        return improved

    # ==================================================================
    # Адреса
    # ==================================================================

    def _assign_addresses(
        self, placements: Dict[str, str], leftovers: List[Pallet]
    ) -> Tuple[List[OperationSchema], List[NotPlacedSchema]]:
        all_ops = []
        by_section: Dict[str, List[str]] = defaultdict(list)
        for p_id, sec_id in placements.items():
            by_section[sec_id].append(p_id)

        for sec_id, p_ids in by_section.items():
            section = next((s for s in self.sections if s.id == sec_id), None)
            if section is None:
                continue
            real_addrs = self._sec_addresses.get(sec_id, ["", "", ""])
            sorted_ids = sorted(p_ids, key=lambda pid: self.pallet_map[pid].width, reverse=True)
            W = section.width
            occupied = set()
            occupied_widths = {}

            for p_id in sorted_ids:
                p = self.pallet_map[p_id]
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
                    if idx not in occupied and real_addrs[idx]:
                        assigned = idx; break
                if assigned is None:
                    for idx in range(3):
                        if idx == 1:
                            if 0 in occupied and occupied_widths.get(0, 0) > W / 3:
                                continue
                            if 2 in occupied and occupied_widths.get(2, 0) > W / 3:
                                continue
                        if idx not in occupied and real_addrs[idx]:
                            assigned = idx; break

                if assigned is not None:
                    occupied.add(assigned)
                    occupied_widths[assigned] = w
                    if assigned in (0, 2) and w > W / 3:
                        occupied.add(1)
                        occupied_widths[1] = w
                    all_ops.append(OperationSchema(
                        pallet=p_id, operation="PUT",
                        newAddress=real_addrs[assigned],
                        sequence=len(all_ops) + 1,
                    ))
                else:
                    placements.pop(p_id, None)

        addressed = {op.pallet for op in all_ops}
        for p_id in list(placements.keys()):
            if p_id not in addressed:
                placements.pop(p_id, None)

        not_placed = [NotPlacedSchema(pallet=p.id, reason="NO_SPACE") for p in leftovers]
        # Добавляем паллеты, которые не получили адрес
        unaddressed_ids = set(placements.keys()) - addressed
        not_placed.extend(NotPlacedSchema(pallet=pid, reason="NO_SPACE")
                         for pid in unaddressed_ids)

        return all_ops, not_placed

    # ==================================================================
    # Ответ
    # ==================================================================

    def _build_response(self, ops, not_placed, elapsed, placed) -> OptimizationResponse:
        return OptimizationResponse(
            optimizationId="hybrid-v4",
            mode="place",
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.COMPLETE if not not_placed else PlacementStatus.PARTIAL,
            score=float(len(ops) * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=ops,
            notPlaced=not_placed,
            metrics=MetricsSchema(
                placedPallets=len(ops),
                movedPallets=0,
                notPlacedPallets=len(not_placed),
                potentialLoss=0,
                usedSections=len(set(op.newAddress for op in ops)),
            ),
        )


def run_hybrid_v4(request: OptimizationRequest) -> OptimizationResponse:
    return HybridV4Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=request.settings,
    ).solve()
