"""Hybrid V10: V3 cold start + address-aware CP-SAT reslot.

Pass 1: V3 BFD + chain-swap (~3215 паллет, ~4с, 0 ошибок).
Pass 2: Address-aware CP-SAT for leftovers only (no reslot of existing).
         pallet → address напрямую. Быстрая модель (<3K переменных).
"""
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model as ort_cp_model

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
from solver.hybrid_v3 import HybridV3Solver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Address-aware CP-SAT (leftovers only, no reslot)
# ---------------------------------------------------------------------------

def _address_cpsat_place(
    occ2: List[OccupancySectionSchema],
    leftovers: List[NewPalletSchema],
    settings: OptimizationSettingsSchema,
) -> Tuple[List[OperationSchema], List[NotPlacedSchema]]:
    """CP-SAT размещает leftovers напрямую на свободные адреса.

    Без реслота — только пустые адреса. Быстрая модель.
    """
    sections, addresses, _ = build_warehouse_state(occ2)
    section_by_id = {s.id: s for s in sections}

    leftover_pallets = [
        Pallet(id=p.id, type_size=PalletTypeSize(
            width=p.width, height=p.height, depth=p.depth, weight=p.weight))
        for p in leftovers
    ]
    pallet_map = {p.id: p for p in leftover_pallets}

    # Только свободные адреса
    free_addrs = [a for a in addresses if a.pallet_id is None]
    if not free_addrs:
        return [], [NotPlacedSchema(pallet=p.id, reason="NO_FREE_ADDRESSES")
                    for p in leftover_pallets]

    # Строим карту: секция → [свободные адреса]
    sec_free: Dict[str, List] = defaultdict(list)
    for a in free_addrs:
        sec_free[a.section_id].append(a)

    # Группируем адреса по секциям для constraints
    sec_info: Dict[str, dict] = {}
    for sec_id, addrs in sec_free.items():
        sec = section_by_id[sec_id]
        # Текущие паллеты в секции
        occupied_addrs = [a for a in addresses
                         if a.section_id == sec_id and a.pallet_id is not None]
        occupied_widths = []
        occupied_weights = []
        for a in occupied_addrs:
            # Найти pallet данные из occ2
            for row in occ2:
                if row.section_id == sec_id:
                    for i, addr_field in enumerate([row.address1, row.address2, row.address3]):
                        if addr_field == a.id:
                            w = getattr(row, f"pallet{i+1}_width", 0) or 0
                            h = getattr(row, f"pallet{i+1}_weight", 0) or 0
                            if w > 0:
                                occupied_widths.append(w)
                                occupied_weights.append(h)
                            break

        sec_info[sec_id] = {
            'section': sec,
            'free_addrs': addrs,
            'occupied_count': len(occupied_addrs),
            'occupied_width': sum(occupied_widths),
            'occupied_weight': sum(occupied_weights),
            'occupied_widths_list': occupied_widths,
        }

    # Совместимость: pallet → [free addresses]
    compat: Dict[str, List] = {}
    for p in leftover_pallets:
        comp = []
        for a in free_addrs:
            sec = section_by_id[a.section_id]
            # Basic fits
            if p.height > sec.height:
                continue
            if p.depth > sec.depth:
                continue
            if p.weight > sec.max_lift_weight:
                continue
            if p.width > sec.eff_max_width:
                continue
            if sec.eff_max_depth > 0 and p.depth > sec.eff_max_depth:
                continue
            if settings.strictNarrowAislePlacement and sec.narrow_aisle and not p.is_narrow:
                continue
            # Position constraint
            if p.width > sec.width * 2 / 3 and a.position != 2:
                continue
            if p.width > sec.width / 3 and a.position == 2:
                continue
            # Center blocking: position 2 blocked if edge has wide pallet
            if a.position == 2:
                blocked = False
                info = sec_info[sec.id]
                for ow in info['occupied_widths_list']:
                    if ow > sec.width / 3:
                        blocked = True
                        break
                if blocked:
                    continue  # skip this address, don't wipe entire comp list
            comp.append(a)
        if comp:
            compat[p.id] = comp

    leftovers_with_space = [p for p in leftover_pallets if compat.get(p.id)]
    if not leftovers_with_space:
        return [], [NotPlacedSchema(pallet=p.id, reason="NO_COMPATIBLE_ADDRESS")
                    for p in leftover_pallets]

    print(f"  Addr-CP-SAT: {len(leftovers_with_space)}/{len(leftovers)} leftovers "
          f"have compatible addresses ({len(free_addrs)} free addrs)")

    # Diagnostic: can ANY leftover fit in ANY free address width-wise?
    diag_count = 0
    for p in leftovers_with_space[:5]:
        for a in compat.get(p.id, [])[:3]:
            sec = section_by_id[a.section_id]
            info = sec_info[sec.id]
            occ_w = info['occupied_width']
            occ_n = info['occupied_count']
            gap = sec.gap_width
            need = occ_w + p.width + (occ_n + 1 + 1) * gap
            fits = need <= sec.width
            if diag_count < 5:
                print(f"    DIAG {p.id} W={p.width} -> addr {a.id} pos={a.position} "
                      f"sec_w={sec.width} occ_w={occ_w} occ_n={occ_n} gap={gap} "
                      f"need={need} fits={fits}")
                diag_count += 1

    # Build model
    model = ort_cp_model.CpModel()
    x: Dict[Tuple[str, str], any] = {}  # (pallet_id, addr_id) → BoolVar

    for p in leftovers_with_space:
        for a in compat.get(p.id, []):
            x[(p.id, a.id)] = model.NewBoolVar(f"x_{p.id}_{a.id}")

    print(f"  Model: {len(x)} vars")

    # Each pallet ≤ 1
    for p in leftovers_with_space:
        vars_p = [x[(p.id, a.id)] for a in compat.get(p.id, []) if (p.id, a.id) in x]
        if vars_p:
            model.Add(sum(vars_p) <= 1)

    # Each address ≤ 1
    addr_to_vars: Dict[str, list] = defaultdict(list)
    for (pid, aid), v in x.items():
        addr_to_vars[aid].append(v)
    for aid, vs in addr_to_vars.items():
        if len(vs) > 1:
            model.Add(sum(vs) <= 1)

    # Section constraints
    sec_vars: Dict[str, List[Tuple[str, str, any]]] = defaultdict(list)
    for (pid, aid), v in x.items():
        for a in free_addrs:
            if a.id == aid:
                sec_vars[a.section_id].append((pid, aid, v))
                break

    for sec_id, pvs in sec_vars.items():
        info = sec_info[sec_id]
        sec = info['section']
        gap = sec.gap_width
        occ_count = info['occupied_count']
        occ_width = info['occupied_width']
        occ_weight = info['occupied_weight']

        # Count: occupied + new ≤ max_pallets
        new_count = sum(v for _, _, v in pvs)
        model.Add(occ_count + new_count <= sec.max_pallets)

        # Width: occupied_width + new_widths + (occupied + new + 1) * gap ≤ section_width
        width_terms = []
        for pid, aid, v in pvs:
            p = pallet_map[pid]
            width_terms.append(int(p.width) * v)

        total_width_expr = sum(width_terms) + int(occ_width)
        gap_expr = (occ_count + new_count + 1) * int(gap)
        model.Add(total_width_expr + gap_expr <= int(sec.width))

        # Weight
        if sec.max_weight < 1e9:
            weight_terms = [int(pallet_map[pid].weight) * v for pid, _, v in pvs]
            model.Add(sum(weight_terms) + int(occ_weight) <= int(sec.max_weight))

    # Objective
    objective_terms = []
    for p in leftovers_with_space:
        for a in compat.get(p.id, []):
            if (p.id, a.id) in x:
                objective_terms.append(x[(p.id, a.id)])

    if not objective_terms:
        return [], [NotPlacedSchema(pallet=p.id, reason="NO_COMPATIBLE_ADDRESS")
                    for p in leftover_pallets]

    model.Maximize(sum(objective_terms))

    solver = ort_cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = min(8.0, settings.timeLimitSeconds * 0.4)
    solver.parameters.num_search_workers = 4
    solver.parameters.log_search_progress = False

    status = solver.Solve(model)
    print(f"  CP-SAT: {status}, obj={solver.ObjectiveValue():.0f}, "
          f"wall={solver.WallTime():.1f}s")

    if status not in (ort_cp_model.OPTIMAL, ort_cp_model.FEASIBLE):
        return [], [NotPlacedSchema(pallet=p.id, reason="CP_SAT_NO_SOLUTION")
                    for p in leftover_pallets]

    # Extract assignments
    ops: List[OperationSchema] = []
    placed_ids: set = set()
    seq = 0

    for p in leftover_pallets:
        for a in compat.get(p.id, []):
            if (p.id, a.id) in x and solver.Value(x[(p.id, a.id)]) == 1:
                seq += 1
                ops.append(OperationSchema(
                    pallet=p.id, operation="PUT",
                    newAddress=a.id, sequence=seq,
                ))
                placed_ids.add(p.id)
                break

    not_placed = [
        NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
        for p in leftover_pallets if p.id not in placed_ids
    ]

    print(f"  Result: {len(ops)} PUTs, {len(not_placed)} not-placed")
    return ops, not_placed


# ---------------------------------------------------------------------------
# Occupancy builder
# ---------------------------------------------------------------------------

def _build_occupancy_from_ops(
    occ: List[OccupancySectionSchema],
    pallet_map: Dict[str, Pallet],
    operations: List[OperationSchema],
) -> List[OccupancySectionSchema]:
    """Occupancy где размещённые паллеты занимают правильные адреса."""
    addr_pallet: Dict[str, str] = {}
    for op in operations:
        if op.operation == "PUT":
            addr_pallet[op.newAddress] = op.pallet

    modified_occ = []
    for row in occ:
        row_dict = row.model_dump()
        for i in range(1, 4):
            for field in ("id", "code", "width", "height", "depth", "weight"):
                row_dict[f"pallet{i}_{field}"] = "" if field in ("id", "code") else 0
            row_dict[f"quantity{i}"] = 0

        addrs = [row.address1, row.address2, row.address3]
        for i, addr in enumerate(addrs):
            idx = i + 1
            p_id = addr_pallet.get(addr, "")
            if p_id:
                p = pallet_map.get(p_id)
                if p:
                    row_dict[f"pallet{idx}_id"] = p.id
                    row_dict[f"pallet{idx}_code"] = p.id
                    row_dict[f"pallet{idx}_width"] = p.width
                    row_dict[f"pallet{idx}_height"] = p.height
                    row_dict[f"pallet{idx}_depth"] = p.depth
                    row_dict[f"pallet{idx}_weight"] = p.weight
                    row_dict[f"quantity{idx}"] = 1
        modified_occ.append(OccupancySectionSchema(**row_dict))
    return modified_occ


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_hybrid_v10(request: OptimizationRequest) -> OptimizationResponse:
    """V10: V3 cold start + address-aware CP-SAT для leftovers."""
    t0 = time.time()
    total_new = len(request.newPallets)

    # === Pass 1: V3 холодный старт ===
    settings1 = request.settings.model_copy(update={
        "allowReslot": False,
        "maxOperations": max(request.settings.maxOperations, total_new + 1000),
    })
    v3 = HybridV3Solver(
        occupancy=request.occupancy,
        new_pallets=request.newPallets,
        settings=settings1,
    )
    v3._phase_bfd()
    v3._phase_chain_swap()
    v3._phase_micro_cpsat()

    operations1, _ = v3._assign_addresses()
    placed1 = len([op for op in operations1 if op.operation == "PUT"])
    moved1 = len([op for op in operations1 if op.operation == "MOVE"])

    placed_ids_pass1 = {op.pallet for op in operations1 if op.operation == "PUT"}
    leftovers = [p for p in request.newPallets if p.id not in placed_ids_pass1]

    print(f"  Pass 1: {placed1} placed, {len(leftovers)} leftovers "
          f"({time.time() - t0:.1f}s)")

    if not leftovers:
        elapsed = time.time() - t0
        return OptimizationResponse(
            optimizationId=request.optimizationId or "hybrid-v10",
            mode=request.mode,
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.COMPLETE,
            score=float(placed1 * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations1,
            notPlaced=[],
            metrics=MetricsSchema(
                placedPallets=placed1, movedPallets=moved1,
                notPlacedPallets=0, potentialLoss=0,
                usedSections=len(set(op.newAddress for op in operations1)),
            ),
        )

    # === Pass 2: Address-aware CP-SAT ===
    occ2 = _build_occupancy_from_ops(request.occupancy, v3.pallet_map, operations1)

    ops2, not_placed2 = _address_cpsat_place(occ2, leftovers, request.settings)

    placed2 = len([op for op in ops2 if op.operation == "PUT"])

    # Dry-run merge
    virtual: Dict[str, str] = {}
    for op in operations1:
        virtual[op.newAddress] = op.pallet

    valid_ops2 = []
    dropped = 0
    for op in ops2:
        if virtual.get(op.newAddress):
            dropped += 1
            continue
        virtual[op.newAddress] = op.pallet
        valid_ops2.append(op)

    if dropped > 0:
        print(f"  Dry-run: dropped {dropped} conflicting ops")

    all_operations = list(operations1) + valid_ops2
    for i, op in enumerate(all_operations):
        op.sequence = i + 1

    total_placed = placed1 + len(valid_ops2)
    elapsed = time.time() - t0

    placed_in_pass2 = {op.pallet for op in valid_ops2}
    not_placed_all = [
        NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
        for p in leftovers if p.id not in placed_in_pass2
    ]

    print(f"  TOTAL: {total_placed}/{total_new} ({total_placed/total_new*100:.1f}%) "
          f"placed, {len(not_placed_all)} not-placed, {elapsed:.1f}s")

    return OptimizationResponse(
        optimizationId=request.optimizationId or "hybrid-v10",
        mode=request.mode,
        solverStatus=SolverStatus.FEASIBLE,
        placementStatus=PlacementStatus.COMPLETE if not not_placed_all else PlacementStatus.PARTIAL,
        score=float(total_placed * 100000),
        executionTimeSeconds=round(elapsed, 1),
        operations=all_operations,
        notPlaced=not_placed_all,
        metrics=MetricsSchema(
            placedPallets=total_placed,
            movedPallets=0,
            notPlacedPallets=len(not_placed_all),
            potentialLoss=0,
            usedSections=len(set(op.newAddress for op in all_operations)),
        ),
    )
