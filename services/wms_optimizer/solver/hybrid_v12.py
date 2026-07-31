"""Hybrid V12: Rating-guided multi-start BFD + V3 chain-swap + micro CP-SAT.

Key innovation: вместо best-fit (минимизация остатка), используем Cell Liquidity
Index (CLI) для выбора секции. Цель — избегать "мёртвой зоны" (700-1700mm
свободной ширины), где секция уже не вмещает W>=1600, но ещё не заполнена.

Три стратегии BFD, лучший результат проходит дальше:
1. Standard: текущий V3 (type-groups, BFD)
2. CLI-guided: width-descending, максимизация CLI после размещения
3. Pair-first: широкие паллеты — в секции с узкой паллетой, узкие — к широким

Pallet Illiquidity Index (PII) = width. Чем шире паллета, тем труднее разместить.
Cell Liquidity Index (CLI) = f(free_width_after). free_w>=1700 → HIGH (можно W>=1600).
"""
import logging
import time
from collections import defaultdict
from copy import deepcopy
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

GAP = 50.0


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


# ---------------------------------------------------------------------------
# Cell Liquidity Index
# ---------------------------------------------------------------------------

def _cli(free_width: float) -> float:
    """Cell Liquidity Index: насколько полезна секция для будущих размещений.

    Возвращает score (выше = лучше для будущих паллет):
    - free_w >= 1700: HIGH — вмещает W>=1600 (самые трудные leftover'ы)
    - free_w >= 1000: MEDIUM — вмещает W<=900 (~50% паллет)
    - free_w < 700:   TIGHT — секция хорошо заполнена (мало потерь)
    - 700..1000:      LOW — может вместить только редкие W<=650
    - 1000..1700:     DEAD — не вмещает W>=1600, теряет место
    """
    if free_width >= 1700:
        return 100.0 + free_width  # Premium: can fit W>=1600
    elif free_width >= 1000:
        return 50.0 + (1700 - free_width) / 700 * 30  # 50-80: can fit W<=900
    elif free_width < 500:
        return 30.0 + free_width / 500 * 20  # 30-50: well-packed, low waste
    else:
        return free_width / 500 * 30  # 0-30: dead zone, more is worse


def _placement_cli(section: Section, existing_pallets: List[Pallet], new_pallet: Pallet) -> float:
    """CLI after hypothetically placing new_pallet in section."""
    current_w = sum(p.width for p in existing_pallets)
    n = len(existing_pallets)
    new_free = section.width - current_w - new_pallet.width - (n + 2) * GAP
    return _cli(new_free)


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class HybridV12Solver:
    """Rating-guided multi-start BFD + chain-swap + micro CP-SAT."""

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

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

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
    # Snapshot / restore (for multi-start)
    # ------------------------------------------------------------------

    def _take_snapshot(self):
        return (
            deepcopy(self.section_states),
            dict(self.placements),
            list(self.new_pallets),
        )

    def _restore_snapshot(self, snapshot):
        self.section_states, self.placements, self.new_pallets = snapshot

    # ------------------------------------------------------------------
    # Main solve
    # ------------------------------------------------------------------

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.all_pallets)

        # Phase 1: Multi-start BFD
        n1 = self._phase_bfd()
        logger.info(f"  [BFD]         {n1}/{total} ({n1/total*100:.1f}%)")

        # Phase 2: Chain-Swap
        n2 = self._phase_chain_swap()
        logger.info(f"  [Chain-Swap]  {n2}/{total} ({n2/total*100:.1f}%)")

        # Phase 3: Micro CP-SAT
        n3 = self._phase_micro_cpsat()
        logger.info(f"  [CP-SAT]      {n3}/{total} ({n3/total*100:.1f}%)")

        # Phase 4: Address assignment
        operations, not_placed = self._assign_addresses()

        elapsed = time.time() - t0
        placed_count = len([op for op in operations if op.operation == "PUT"])
        moved_count = len([op for op in operations if op.operation == "MOVE"])
        logger.info(f"Hybrid V12: итог {placed_count} placed + {moved_count} moved / {total} new за {elapsed:.1f}с")
        return self._build_response(operations, not_placed, elapsed, placed_count, moved_count)

    # ------------------------------------------------------------------
    # Phase 1: Multi-start BFD
    # ------------------------------------------------------------------

    def _phase_bfd(self) -> int:
        """Multi-start BFD: 3 стратегии, лучший результат."""
        initial_snapshot = self._take_snapshot()

        strategies = [
            ("standard", self._bfd_standard),
            ("cli-guided", self._bfd_cli_guided),
            ("pair-wide-narrow", self._bfd_pair_first),
        ]

        best_n = -1
        best_snapshot = None
        best_name = "none"

        for name, strategy in strategies:
            self._restore_snapshot(initial_snapshot)
            n = strategy()
            logger.info(f"    BFD/{name}: {n}/{len(self.all_pallets)} ({n/len(self.all_pallets)*100:.1f}%)")
            if n > best_n:
                best_n = n
                best_snapshot = self._take_snapshot()
                best_name = name

        self._restore_snapshot(best_snapshot)
        logger.info(f"  BFD best: {best_name} = {best_n}")
        return best_n

    # --- Strategy 1: Standard (V3 current) ---

    def _bfd_standard(self) -> int:
        """Best-Fit Decreasing: типогруппы → best-fit."""
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
                    rem = state.free_width - (pallet.width + state.gap_width)
                    if rem < min_rem:
                        min_rem = rem
                        best_id = sec_id
                if best_id:
                    self._do_place(pallet.id, best_id)
                    placed_ids.add(pallet.id)

        self.new_pallets = [p for p in self.new_pallets if p.id not in placed_ids]
        return len(placed_ids)

    # --- Strategy 2: CLI-guided ---

    def _bfd_cli_guided(self) -> int:
        """CLI-guided BFD: паллеты ширина-desc, максимизация CLI после размещения.

        Для каждой паллеты выбираем секцию, максимизирующую CLI.
        Это избегает "мёртвой зоны" (700-1700mm free_w) и создаёт больше
        "жидких" секций (free_w >= 1700) для будущих широких паллет.
        """
        sorted_pallets = sorted(self.new_pallets, key=lambda p: (-p.width, -p.height, -p.weight))

        placed_ids = set()
        for pallet in sorted_pallets:
            best_id = None
            best_score = -1e9

            for sec_id in self.compatible[pallet.id]:
                state = self.section_states[sec_id]
                if state.free_count <= 0:
                    continue
                if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                    continue

                cli = _placement_cli(state.section, state.placed_pallets, pallet)
                # Tie-break: prefer sections that already have pallets (consolidation)
                # and sections with higher free_count remaining
                occupied = len(state.placed_pallets)
                score = cli + occupied * 0.1 + state.free_count * 0.01

                if score > best_score:
                    best_score = score
                    best_id = sec_id

            if best_id:
                self._do_place(pallet.id, best_id)
                placed_ids.add(pallet.id)

        self.new_pallets = [p for p in self.new_pallets if p.id not in placed_ids]
        return len(placed_ids)

    # --- Strategy 3: Pair-first ---

    def _bfd_pair_first(self) -> int:
        """Pair-first: широкие паллеты — в секции с узкой, узкие — к широким.

        Фаза A: широкие паллеты (W>=1600) → prefer секции где уже есть W<=900 и есть место
        Фаза B: средние паллеты (900<W<1600) → обычный best-fit
        Фаза C: узкие паллеты (W<=900) → prefer секции где уже есть W>=1600 и есть место
        """
        wide = [p for p in self.new_pallets if p.width >= 1600]
        medium = [p for p in self.new_pallets if 900 < p.width < 1600]
        narrow = [p for p in self.new_pallets if p.width <= 900]

        placed_ids = set()

        # Phase A: Wide pallets → sections with narrow pallets
        for pallet in sorted(wide, key=lambda p: -p.width):
            best_id = None
            best_score = -1e9

            for sec_id in self.compatible[pallet.id]:
                state = self.section_states[sec_id]
                if state.free_count <= 0:
                    continue
                if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                    continue

                # Prefer sections that ALREADY have narrow pallets
                has_narrow = any(p.width <= 900 for p in state.placed_pallets)
                has_wide = any(p.width >= 1600 for p in state.placed_pallets)
                n_existing = len(state.placed_pallets)

                if has_wide:
                    continue  # Don't put two wide pallets together if avoidable

                cli = _placement_cli(state.section, state.placed_pallets, pallet)
                # Strong bonus for pairing with narrow
                score = cli + (10.0 if has_narrow else 0) + n_existing * 0.1

                if score > best_score:
                    best_score = score
                    best_id = sec_id

            # Fallback: any compatible section
            if best_id is None:
                for sec_id in self.compatible[pallet.id]:
                    state = self.section_states[sec_id]
                    if state.free_count <= 0:
                        continue
                    if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                        continue
                    has_wide = any(p.width >= 1600 for p in state.placed_pallets)
                    if has_wide:
                        continue
                    cli = _placement_cli(state.section, state.placed_pallets, pallet)
                    if cli > best_score:
                        best_score = cli
                        best_id = sec_id

            if best_id:
                self._do_place(pallet.id, best_id)
                placed_ids.add(pallet.id)

        # Phase B: Medium pallets → best-fit
        for pallet in sorted(medium, key=lambda p: -p.width):
            best_id = None
            min_rem = float("inf")
            for sec_id in self.compatible[pallet.id]:
                state = self.section_states[sec_id]
                if state.free_count <= 0:
                    continue
                if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                    continue
                rem = state.free_width - (pallet.width + state.gap_width)
                if rem < min_rem:
                    min_rem = rem
                    best_id = sec_id
            if best_id:
                self._do_place(pallet.id, best_id)
                placed_ids.add(pallet.id)

        # Phase C: Narrow pallets → sections with wide pallets (fill gaps)
        for pallet in sorted(narrow, key=lambda p: -p.width):
            best_id = None
            best_score = -1e9

            for sec_id in self.compatible[pallet.id]:
                state = self.section_states[sec_id]
                if state.free_count <= 0:
                    continue
                if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                    continue

                has_wide = any(p.width >= 1600 for p in state.placed_pallets)
                n_existing = len(state.placed_pallets)

                cli = _placement_cli(state.section, state.placed_pallets, pallet)
                # Bonus for joining wide pallet (filling the section)
                score = cli + (10.0 if has_wide else 0) + n_existing * 0.1

                if score > best_score:
                    best_score = score
                    best_id = sec_id

            # Fallback: best-fit
            if best_id is None:
                min_rem = float("inf")
                for sec_id in self.compatible[pallet.id]:
                    state = self.section_states[sec_id]
                    if state.free_count <= 0:
                        continue
                    if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                        continue
                    rem = state.free_width - (pallet.width + state.gap_width)
                    if rem < min_rem:
                        min_rem = rem
                        best_id = sec_id

            if best_id:
                self._do_place(pallet.id, best_id)
                placed_ids.add(pallet.id)

        self.new_pallets = [p for p in self.new_pallets if p.id not in placed_ids]
        return len(placed_ids)

    # ------------------------------------------------------------------
    # Phase 2-4: Chain-Swap, CP-SAT, Addresses (same as V3)
    # ------------------------------------------------------------------

    def _phase_chain_swap(self) -> int:
        """Iterative chain-swap (exact V3 code)."""
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
        """Chain search: depth-0, depth-1, depth-2 (exact V3 code)."""
        compatible = self.compatible.get(leftover.id, [])
        if not compatible:
            return None

        # --- Depth 0: direct placement ---
        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            if state_a.free_width >= leftover.width + state_a.gap_width:
                return [("place", leftover.id, sec_a_id)]

        # --- Depth 1: remove 1 pallet from A, place leftover in A, place A's pallet in B ---
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
                        return [
                            ("remove", placed_a.id, sec_a_id),
                            ("place", leftover.id, sec_a_id),
                            ("place", placed_a.id, sec_b_id),
                        ]

        # --- Depth 2: remove 2 pallets ---
        MAX_LEVEL2_CHECKS = 2000
        checks = 0
        for sec_a_id in compatible[:20]:
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

                for sec_b_id in self.compatible.get(placed_a.id, [])[:20]:
                    if sec_b_id == sec_a_id:
                        continue
                    state_b = self.section_states[sec_b_id]
                    if state_b.free_count <= 0:
                        continue
                    gap_b = state_b.gap_width
                    req_b = placed_a.width + gap_b

                    if state_b.free_width >= req_b:
                        return [
                            ("remove", placed_a.id, sec_a_id),
                            ("place", leftover.id, sec_a_id),
                            ("place", placed_a.id, sec_b_id),
                        ]

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

    def _execute_chain(self, chain: List[Tuple[str, str, str]]):
        """Atomic chain execution (exact V3 code)."""
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

    def _phase_micro_cpsat(self) -> int:
        if not self.new_pallets or len(self.new_pallets) > 200:
            return len(self.placements)

        leftover = self.new_pallets[:200]
        model = cp_model.CpModel()
        x = {}
        for p in leftover:
            for sec_id in self.compatible.get(p.id, []):
                state = self.section_states[sec_id]
                if state.free_count <= 0:
                    continue
                x[(p.id, sec_id)] = model.NewBoolVar(f"x_{p.id}_{sec_id}")

        if not x:
            return len(self.placements)

        for p in leftover:
            vars_p = [x[(p.id, s)] for s in self.compatible.get(p.id, []) if (p.id, s) in x]
            if vars_p:
                model.Add(sum(vars_p) <= 1)

        sec_vars: Dict[str, list] = defaultdict(list)
        for (pid, sid), v in x.items():
            sec_vars[sid].append((pid, v))

        for sid, pvs in sec_vars.items():
            state = self.section_states[sid]
            n_existing = len(state.placed_pallets)
            model.Add(sum(v for _, v in pvs) <= state.free_count)
            w_existing = int(sum(p.width for p in state.placed_pallets))
            w_new = sum(int(self.pallet_map[pid].width) * v for pid, v in pvs)
            gap_total = (n_existing + sum(v for _, v in pvs) + 1) * int(state.gap_width)
            model.Add(w_existing + w_new + gap_total <= int(state.section.width))

        model.Maximize(sum(x.values()))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = min(5.0, self.time_limit * 0.3)
        solver.parameters.num_search_workers = 4
        solver.parameters.log_search_progress = False

        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return len(self.placements)

        placed = 0
        for p in leftover:
            for sec_id in self.compatible.get(p.id, []):
                if (p.id, sec_id) in x and solver.Value(x[(p.id, sec_id)]) == 1:
                    self._do_place(p.id, sec_id)
                    placed += 1
                    break

        self.new_pallets = [p for p in self.new_pallets if p.id not in self.placements]
        return len(self.placements)

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _do_place(self, pallet_id: str, section_id: str):
        state = self.section_states[section_id]
        pallet = self.pallet_map[pallet_id]
        state.free_width -= (pallet.width + state.gap_width)
        state.free_count -= 1
        state.placed_pallets.append(pallet)
        self.placements[pallet_id] = section_id

    def _do_remove(self, pallet_id: str, section_id: str):
        state = self.section_states[section_id]
        pallet = self.pallet_map.get(pallet_id) or self.existing_pallet_map.get(pallet_id)
        if pallet is None:
            return
        state.free_width += pallet.width + state.gap_width
        state.free_count += 1
        state.placed_pallets = [p for p in state.placed_pallets if p.id != pallet_id]
        self.placements.pop(pallet_id, None)

    # ------------------------------------------------------------------
    # Address assignment (same as V3)
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

    # ------------------------------------------------------------------
    # Response
    # ------------------------------------------------------------------

    def _build_response(
        self, operations, not_placed, elapsed, placed_count, moved_count=0,
    ) -> OptimizationResponse:
        return OptimizationResponse(
            optimizationId="hybrid-v12",
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


def run_hybrid_v12(request: OptimizationRequest) -> OptimizationResponse:
    return HybridV12Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=request.settings,
    ).solve()
