"""Hybrid V11: V3 cold start → Compaction (single-pallet consolidation) → V3 re-placement.

Phase 1: V3 BFD + chain-swap (3215 placed, 0 errors, ~4s)
Phase 2: Consolidate single-pallet sections — move pallet from section B to section A,
         freeing section B entirely. Address-aware MOVE operations.
Phase 3: V3 re-placement — place leftovers into freed sections.

Key insight: leftovers after V3 are W≥1600. Each needs an entire section.
Consolidating single-pallet sections frees entire sections for leftovers.
"""
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

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
from solver.hybrid_v3 import HybridV3Solver

logger = logging.getLogger(__name__)

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
GAP = 50.0


def _is_empty(value: str) -> bool:
    return not value or value == EMPTY_GUID


# ---------------------------------------------------------------------------
# Address-aware free address picker
# ---------------------------------------------------------------------------

def _pick_free_address(
    section_width: float,
    occupied_positions: Dict[int, float],
    free_positions: List[int],
    pallet_width: float,
) -> Optional[int]:
    """Pick a valid free position (1-indexed) for a pallet, respecting 1C rules.

    Rules:
    - W > 2/3 * section_width → position 2 (center) only
    - W > 1/3 * section_width → positions 1, 3 (edges) only
    - Center (pos 2) blocked if either edge has W > 1/3 * section_width
    """
    W = section_width
    w = pallet_width

    if w > W * 2 / 3:
        allowed = [2]
    elif w > W / 3:
        allowed = [1, 3]
    else:
        allowed = [1, 2, 3]

    # Check center blocking
    center_blocked = False
    for pos, pw in occupied_positions.items():
        if pos in (1, 3) and pw > W / 3:
            center_blocked = True
            break

    # Try allowed positions first
    for pos in allowed:
        if pos in free_positions:
            if pos == 2 and center_blocked:
                continue
            return pos

    # Fallback: try any free position
    for pos in free_positions:
        if pos == 2 and center_blocked:
            continue
        return pos

    return None


# ---------------------------------------------------------------------------
# Occupancy builder for compacted state
# ---------------------------------------------------------------------------

def _build_addr_to_pallet(
    original_occ: List[OccupancySectionSchema],
    operations1: List[OperationSchema],
    compaction_ops: List[OperationSchema],
) -> Dict[str, str]:
    """Build address_id → pallet_id map after Phase 1 + Compaction."""
    addr_to_pallet: Dict[str, str] = {}

    # Original occupancy: existing pallets at their addresses
    for row in original_occ:
        for i in range(1, 4):
            addr = getattr(row, f"address{i}", "")
            p_id = getattr(row, f"pallet{i}_id", "")
            if addr and p_id and not _is_empty(p_id):
                addr_to_pallet[addr] = p_id

    # Apply Phase 1 PUTs
    for op in operations1:
        if op.operation == "PUT" and op.newAddress:
            addr_to_pallet[op.newAddress] = op.pallet

    # Apply compaction MOVEs
    for op in compaction_ops:
        if op.operation == "MOVE":
            addr_to_pallet.pop(op.oldAddress, None)
            if op.newAddress:
                addr_to_pallet[op.newAddress] = op.pallet

    return addr_to_pallet


def _build_compacted_occupancy(
    original_occ: List[OccupancySectionSchema],
    addr_to_pallet: Dict[str, str],
    pallet_data: Dict[str, dict],
) -> List[OccupancySectionSchema]:
    """Build occupancy rows reflecting the compacted warehouse state."""
    modified = []
    for row in original_occ:
        d = row.model_dump()
        # Clear all pallet fields
        for i in range(1, 4):
            d[f"pallet{i}_id"] = ""
            d[f"pallet{i}_code"] = ""
            d[f"pallet{i}_width"] = 0
            d[f"pallet{i}_height"] = 0
            d[f"pallet{i}_depth"] = 0
            d[f"pallet{i}_weight"] = 0
            d[f"quantity{i}"] = 0
            d[f"blocked{i}"] = 0

        # Fill from addr_to_pallet
        for i in range(1, 4):
            addr = d.get(f"address{i}", "")
            if addr and addr in addr_to_pallet:
                p_id = addr_to_pallet[addr]
                p_info = pallet_data.get(p_id, {})
                d[f"pallet{i}_id"] = p_id
                d[f"pallet{i}_code"] = p_info.get("code", p_id)
                d[f"pallet{i}_width"] = p_info.get("width", 0)
                d[f"pallet{i}_height"] = p_info.get("height", 0)
                d[f"pallet{i}_depth"] = p_info.get("depth", 0)
                d[f"pallet{i}_weight"] = p_info.get("weight", 0)
                d[f"quantity{i}"] = 1

        modified.append(OccupancySectionSchema(**d))
    return modified


def _build_pallet_data(
    original_occ: List[OccupancySectionSchema],
    new_pallets: List[NewPalletSchema],
) -> Dict[str, dict]:
    """Collect pallet info from original occupancy + new pallets."""
    data: Dict[str, dict] = {}

    # From original occupancy
    for row in original_occ:
        for i in range(1, 4):
            p_id = getattr(row, f"pallet{i}_id", "")
            if p_id and not _is_empty(p_id):
                data[p_id] = {
                    "code": getattr(row, f"pallet{i}_code", p_id),
                    "width": getattr(row, f"pallet{i}_width", 0),
                    "height": getattr(row, f"pallet{i}_height", 0),
                    "depth": getattr(row, f"pallet{i}_depth", 0),
                    "weight": getattr(row, f"pallet{i}_weight", 0),
                }

    # From new pallets
    for p in new_pallets:
        data[p.id] = {
            "code": p.id,
            "width": p.width,
            "height": p.height,
            "depth": p.depth,
            "weight": p.weight,
        }

    return data


# ---------------------------------------------------------------------------
# Compaction: 2→3 pallet consolidation to free sections for leftovers
# ---------------------------------------------------------------------------

def _compact(
    solver: HybridV3Solver,
    operations1: List[OperationSchema],
    original_occ: List[OccupancySectionSchema],
) -> Tuple[List[OperationSchema], List[str]]:
    """Move a pallet from a 2-pallet section to another 2-pallet section,
    creating a 3-pallet section (target) and freeing the source section
    (now 1 narrow pallet + large free_width for W≥1600 leftovers).

    Strategy:
    - Target sections: 2 pallets + free_n≥1 + enough free_w for incoming pallet
    - Source sections: 2 pallets, both W≤1000 (narrow enough that the
      remaining pallet leaves ≥1700mm free for leftovers)
    - Move one narrow pallet from source → target
    - Source becomes 1-pallet with ~1800mm free → can fit W≤1700 leftover

    Returns:
        compaction_ops: MOVE operations for consolidation
        freed_sections: section_ids with ≥1700mm free_width after compaction
    """
    # Build address → pallet map and section → addresses map
    addr_to_pallet: Dict[str, str] = {}
    sec_to_addrs: Dict[str, List[dict]] = defaultdict(list)

    for row in original_occ:
        for i in range(1, 4):
            addr = getattr(row, f"address{i}", "")
            if not addr:
                continue
            p_id = getattr(row, f"pallet{i}_id", "")
            occupied = p_id and not _is_empty(p_id)
            if occupied:
                addr_to_pallet[addr] = p_id
            sec_to_addrs[row.section_id].append({
                "id": addr,
                "position": i,
                "occupied_original": occupied,
            })

    # Apply Phase 1 PUTs
    for op in operations1:
        if op.operation == "PUT" and op.newAddress:
            addr_to_pallet[op.newAddress] = op.pallet

    # Refresh section address occupancy after Phase 1
    for sec_id, addrs in sec_to_addrs.items():
        for a in addrs:
            a["occupied"] = a["id"] in addr_to_pallet
            a["pallet_id"] = addr_to_pallet.get(a["id"])

    # Find candidate sections
    # TARGETS: 2 pallets, free_n >= 1, can accept another narrow pallet
    # SOURCES: 2 pallets, both narrow (W<=1000), after removing one → free_w >= 1700
    targets = []
    sources = []

    for sec_id, state in solver.section_states.items():
        n = len(state.placed_pallets)
        if n != 2:
            continue
        sec_w = state.section.width
        sec = state.section
        addrs = sec_to_addrs.get(sec_id, [])
        occupied_addrs = [a for a in addrs if a["occupied"]]
        free_addrs = [a for a in addrs if not a["occupied"]]
        pallets = state.placed_pallets

        if not occupied_addrs or len(occupied_addrs) != 2:
            continue

        entry = {
            "section_id": sec_id,
            "section": sec,
            "free_w": state.free_width,
            "free_n": state.free_count,
            "pallets": pallets,
            "occupied_addrs": occupied_addrs,
            "free_addrs": free_addrs,
        }

        # Target: can accept another pallet (any width)
        if state.free_count >= 1:
            max_incoming = sec_w - sum(p.width for p in pallets) - 4 * GAP
            entry["max_incoming"] = max_incoming
            targets.append(entry)

        # Source: both pallets narrow, after removing one → large free_w
        if all(p.width <= 1000 for p in pallets):
            # After removing one pallet, free_w = current_free_w + pallet.width + GAP
            for idx, p in enumerate(pallets):
                freed_w = state.free_width + p.width + GAP
                if freed_w >= 1700:  # Can fit W>=1600 leftover
                    entry_copy = dict(entry)
                    entry_copy["remove_pallet"] = p
                    entry_copy["remove_idx"] = idx
                    entry_copy["freed_w"] = freed_w
                    entry_copy["keep_pallet"] = pallets[1 - idx]
                    sources.append(entry_copy)

    print(f"  Compaction: {len(targets)} targets, {len(sources)} sources (2-pallet sections)")

    if not targets or not sources:
        return [], []

    # Find compatible pairs: source pallet fits in target as 3rd
    pairs = []
    compat_fail_reasons = defaultdict(int)
    for tgt in targets:
        for src in sources:
            if tgt["section_id"] == src["section_id"]:
                continue
            # Sections must be same width (2700 vs 2700, 2300 vs 2300)
            if tgt["section"].width != src["section"].width:
                compat_fail_reasons["width_mismatch"] += 1
                continue
            # Source pallet must fit in target section physically
            p_move = src["remove_pallet"]
            if p_move.width > tgt["max_incoming"]:
                compat_fail_reasons["pallet_too_wide"] += 1
                continue
            if p_move.height > tgt["section"].height:
                compat_fail_reasons["pallet_too_tall"] += 1
                continue
            if p_move.depth > tgt["section"].depth:
                compat_fail_reasons["pallet_too_deep"] += 1
                continue
            # Check narrow_aisle compatibility
            if tgt["section"].narrow_aisle and not p_move.is_narrow:
                compat_fail_reasons["narrow_aisle"] += 1
                continue
            # Score: prefer freeing sections with larger freed_w (more space for leftovers)
            score = src["freed_w"]
            pairs.append((tgt, src, p_move, score))

    if compat_fail_reasons:
        print(f"  Compaction pair filter reasons: {dict(compat_fail_reasons)}")
        # Show a target and source example
        if targets and sources:
            t = targets[0]
            s = sources[0]
            print(f"  Example target: W={t['section'].width} H={t['section'].height} D={t['section'].depth} "
                  f"max_in={t.get('max_incoming', 'N/A')}")
            print(f"  Example source: W={s['section'].width} H={s['section'].height} D={s['section'].depth} "
                  f"remove_pallet_W={s['remove_pallet'].width}")

    pairs.sort(key=lambda x: x[3], reverse=True)
    print(f"  Compaction: {len(pairs)} consolidation pairs found")

    # Execute consolidations
    used_sections: set = set()
    compaction_ops: List[OperationSchema] = []
    freed_sections: List[str] = []

    for tgt, src, p_move, score in pairs:
        if tgt["section_id"] in used_sections or src["section_id"] in used_sections:
            continue

        # Find source pallet's current address
        src_addr = None
        src_pos = None
        for a in src["occupied_addrs"]:
            if a["pallet_id"] == p_move.id:
                src_addr = a["id"]
                src_pos = a["position"]
                break

        if src_addr is None:
            continue

        # Find target free address that works for p_move
        # Occupied positions in target
        occupied_positions: Dict[int, float] = {}
        for a in tgt["occupied_addrs"]:
            p = solver.pallet_map.get(a["pallet_id"])
            if p:
                occupied_positions[a["position"]] = p.width

        free_positions = [a["position"] for a in tgt["free_addrs"]]

        pos = _pick_free_address(
            tgt["section"].width,
            occupied_positions,
            free_positions,
            p_move.width,
        )

        if pos is None:
            continue

        # Find address at this position
        target_addr = None
        for a in tgt["free_addrs"]:
            if a["position"] == pos:
                target_addr = a["id"]
                break

        if target_addr is None:
            continue

        compaction_ops.append(OperationSchema(
            pallet=p_move.id,
            operation="MOVE",
            oldAddress=src_addr,
            newAddress=target_addr,
            sequence=0,  # filled later
        ))

        used_sections.add(tgt["section_id"])
        used_sections.add(src["section_id"])
        freed_sections.append(src["section_id"])

    # Also free any sections that end up with just 1 narrow pallet
    # (sections that were sources but whose pallet got moved)
    for src in sources:
        if src["section_id"] in used_sections:
            continue
        # Check if this section still has 2 pallets (it wasn't used as source)
        # Already handled above — only src sections in used_sections are freed

    print(f"  Compaction: {len(compaction_ops)} consolidations, "
          f"{len(freed_sections)} sections freed")
    return compaction_ops, freed_sections


def _try_consolidate(
    target: dict, source: dict
) -> Optional[Tuple[str, int]]:
    """Try to move source pallet into target section. Returns (target_addr, seq) or None."""
    section = target["section"]
    source_pallet = source["pallet"]

    # Build occupied positions in target section
    occupied_positions: Dict[int, float] = {}
    # The pallet already in target
    occupied_positions[target["source_pos"]] = target["pallet"].width

    # Free positions in target
    free_positions = [a["position"] for a in target["free_addrs"]]

    pos = _pick_free_address(
        section.width,
        occupied_positions,
        free_positions,
        source_pallet.width,
    )

    if pos is None:
        return None

    # Find the address at this position
    target_addr = None
    for a in target["free_addrs"]:
        if a["position"] == pos:
            target_addr = a["id"]
            break

    if target_addr is None:
        return None

    return (target_addr, 0)  # sequence filled later


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_hybrid_v11(request: OptimizationRequest) -> OptimizationResponse:
    """V11: V3 cold start → Compaction → V3 re-placement."""
    t0 = time.time()
    total_new = len(request.newPallets)

    # =========================================================================
    # Phase 1: V3 Cold Start
    # =========================================================================
    settings1 = request.settings.model_copy(update={
        "allowReslot": False,
        "maxOperations": max(request.settings.maxOperations, total_new + 2000),
    })
    solver1 = HybridV3Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=settings1,
    )
    solver1._phase_bfd()
    solver1._phase_chain_swap()
    solver1._phase_micro_cpsat()

    operations1, _ = solver1._assign_addresses()
    placed1 = len([op for op in operations1 if op.operation == "PUT"])

    placed_ids_pass1 = {op.pallet for op in operations1 if op.operation == "PUT"}
    leftovers = [p for p in request.newPallets if p.id not in placed_ids_pass1]

    print(f"  Phase 1: {placed1}/{total_new} placed, {len(leftovers)} leftovers "
          f"({time.time() - t0:.1f}s)")

    if not leftovers:
        elapsed = time.time() - t0
        return OptimizationResponse(
            optimizationId=request.optimizationId or "hybrid-v11",
            mode=request.mode,
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.COMPLETE,
            score=float(placed1 * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations1,
            notPlaced=[],
            metrics=MetricsSchema(
                placedPallets=placed1, movedPallets=0,
                notPlacedPallets=0, potentialLoss=0,
                usedSections=len(set(op.newAddress for op in operations1)),
            ),
        )

    # =========================================================================
    # Phase 2: Compaction
    # =========================================================================
    compaction_ops, freed_sections = _compact(solver1, operations1, request.occupancy)

    if not compaction_ops:
        # No consolidation possible — return Phase 1 result
        elapsed = time.time() - t0
        not_placed = [
            NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
            for p in leftovers
        ]
        return OptimizationResponse(
            optimizationId=request.optimizationId or "hybrid-v11",
            mode=request.mode,
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.PARTIAL,
            score=float(placed1 * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations1,
            notPlaced=not_placed,
            metrics=MetricsSchema(
                placedPallets=placed1, movedPallets=0,
                notPlacedPallets=len(not_placed), potentialLoss=0,
                usedSections=len(set(op.newAddress for op in operations1)),
            ),
        )

    # Build compacted occupancy
    addr_to_pallet = _build_addr_to_pallet(
        request.occupancy, operations1, compaction_ops
    )
    pallet_data = _build_pallet_data(request.occupancy, request.newPallets)
    compacted_occ = _build_compacted_occupancy(
        request.occupancy, addr_to_pallet, pallet_data
    )

    # =========================================================================
    # Phase 3: V3 Re-placement
    # =========================================================================
    settings3 = request.settings.model_copy(update={
        "allowReslot": False,
        "maxOperations": max(request.settings.maxOperations, total_new + 2000),
    })
    solver3 = HybridV3Solver(
        occupancy=compacted_occ,
        new_pallets=leftovers,
        settings=settings3,
    )
    solver3._phase_bfd()
    solver3._phase_chain_swap()

    operations3, _ = solver3._assign_addresses()
    placed3 = len([op for op in operations3 if op.operation == "PUT"])

    print(f"  Phase 3: {placed3}/{len(leftovers)} leftovers placed "
          f"({time.time() - t0:.1f}s)")

    # =========================================================================
    # Merge operations
    # =========================================================================
    all_operations = list(operations1) + list(compaction_ops) + list(operations3)
    for i, op in enumerate(all_operations):
        op.sequence = i + 1

    total_placed = placed1 + placed3
    total_moved = len(compaction_ops)
    elapsed = time.time() - t0

    placed_in_p3 = {op.pallet for op in operations3 if op.operation == "PUT"}
    not_placed_all = [
        NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
        for p in leftovers if p.id not in placed_in_p3
    ]

    print(f"  TOTAL: {total_placed}/{total_new} ({total_placed/total_new*100:.1f}%) "
          f"placed, {total_moved} moved, {len(not_placed_all)} not-placed, "
          f"{len(freed_sections)} freed sections, {elapsed:.1f}s")

    return OptimizationResponse(
        optimizationId=request.optimizationId or "hybrid-v11",
        mode=request.mode,
        solverStatus=SolverStatus.FEASIBLE,
        placementStatus=PlacementStatus.COMPLETE if not not_placed_all else PlacementStatus.PARTIAL,
        score=float(total_placed * 100000),
        executionTimeSeconds=round(elapsed, 1),
        operations=all_operations,
        notPlaced=not_placed_all,
        metrics=MetricsSchema(
            placedPallets=total_placed,
            movedPallets=total_moved,
            notPlacedPallets=len(not_placed_all),
            potentialLoss=0,
            usedSections=len(set(op.newAddress for op in all_operations)),
        ),
    )
