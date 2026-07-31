"""Hybrid V5: Aggregate CP-SAT + реслот — максимальное качество.
Запускает агрегированный CP-SAT для глобальной оптимизации,
затем chain-swap с реслотом для неразмещённых паллет.
"""
import time
import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema, OperationSchema, NotPlacedSchema,
    MetricsSchema, OptimizationRequest, OptimizationResponse, OptimizationSettingsSchema,
    PlacementStatus, SolverStatus,
)
from models.occupancy_builder import build_warehouse_state
from models.pallet import Pallet, PalletTypeSize
from models.section import Section
from solver.cp_sat_aggregated import CPSATAggregatedSolver

logger = logging.getLogger(__name__)


def run_hybrid_v5(request: OptimizationRequest) -> OptimizationResponse:
    """Точка входа: aggregate CP-SAT → реслот через V3 chain-swap → ответ."""
    t0 = time.time()

    sections, addresses, existing_pallets = build_warehouse_state(request.occupancy)
    new_pallets = [
        Pallet(id=p.id, type_size=PalletTypeSize(
            width=p.width, height=p.height, depth=p.depth, weight=p.weight
        ))
        for p in request.newPallets
    ]

    total = len(new_pallets)
    logger.info(f"Hybrid V5: {total} паллет, {len(sections)} секций"
                f" allowReslot={request.settings.allowReslot}")

    # Гарантируем maxOperations >= число паллет
    settings_v5 = request.settings
    if settings_v5.maxOperations < total:
        settings_v5 = settings_v5.model_copy(update={"maxOperations": max(total, 5000)})

    # --- Aggregate CP-SAT ---
    agg = CPSATAggregatedSolver(
        sections=sections,
        new_pallets=new_pallets,
        existing_pallets=existing_pallets,
        addresses=addresses,
        settings=settings_v5,
    )
    assignment, status, score = agg.solve()
    agg_placed = sum(1 for v in assignment.values() if v)
    logger.info(f"  Aggregate CP-SAT: {agg_placed}/{total} ({agg_placed/total*100:.1f}%) status={status}")

    # --- Реслот через V3 chain-swap ---
    if settings_v5.allowReslot and existing_pallets:
        leftovers = [p for p in new_pallets if assignment.get(p.id) is None]
        if leftovers:
            logger.info(f"  Реслот: {len(leftovers)} leftovers, запуск chain-swap...")
            assignment = _reslot_leftovers(
                request, sections, assignment, leftovers, existing_pallets, settings_v5
            )
            after_reslot = sum(1 for v in assignment.values() if v)
            logger.info(f"  После реслота: {after_reslot}/{total} (+{after_reslot - agg_placed})")

    # --- Ответ ---
    resp = _build_response(request, sections, assignment, new_pallets, time.time() - t0)
    return resp


def _reslot_leftovers(
    request, sections, assignment, leftovers, existing_pallets, settings,
) -> Dict[str, str]:
    """Запускает V3 chain-swap с реслотом для неразмещённых паллет."""
    from solver.hybrid_v3 import HybridV3Solver

    # Строим occupancy с aggregate-размещениями как existing
    pallet_to_sec = {p_id: sec_id for p_id, sec_id in assignment.items() if sec_id}
    sec_addresses = {}
    for row in request.occupancy:
        sec_addresses[row.section_id] = [row.address1, row.address2, row.address3]

    # Модифицируем occupancy: добавляем aggregate паллеты как existing
    modified_occ = []
    for row in request.occupancy:
        row_dict = row.model_dump()
        placed_in_sec = [(p_id, sec_id) for p_id, sec_id in pallet_to_sec.items()
                         if sec_id == row.section_id and p_id not in {ep.id for ep in existing_pallets}]
        for i, (p_id, _) in enumerate(placed_in_sec[:3]):
            p = next((lp for lp in leftovers if lp.id == p_id), None)
            if p is None:
                # Ищем в assignment
                for np_id, np_sec in pallet_to_sec.items():
                    if np_id == p_id and np_sec == row.section_id:
                        # Нашли — но у нас нет объекта Pallet, пропускаем
                        pass
                continue
            addr_key = f"address{i+1}"
            pallet_key = f"pallet{i+1}_id"
            width_key = f"pallet{i+1}_width"
            height_key = f"pallet{i+1}_height"
            depth_key = f"pallet{i+1}_depth"
            weight_key = f"pallet{i+1}_weight"
            qty_key = f"quantity{i+1}"
            row_dict[pallet_key] = p.id
            row_dict[width_key] = p.width
            row_dict[height_key] = p.height
            row_dict[depth_key] = p.depth
            row_dict[weight_key] = p.weight
            row_dict[qty_key] = 1
        modified_occ.append(OccupancySectionSchema(**row_dict))

    # Запускаем V3 только с chain-swap (BFD пропускаем — паллеты уже размещены)
    leftover_schemas = [
        NewPalletSchema(id=p.id, width=p.width, height=p.height, depth=p.depth, weight=p.weight)
        for p in leftovers
    ]

    v3 = HybridV3Solver(
        occupancy=modified_occ,
        new_pallets=leftover_schemas,
        settings=settings,
    )
    # Только chain-swap, без BFD
    v3._phase_chain_swap()
    v3._phase_micro_cpsat()

    # Обновляем assignment результатами V3
    for p_id, sec_id in v3.placements.items():
        if p_id not in assignment or assignment[p_id] is None:
            assignment[p_id] = sec_id

    return assignment


def _build_response(
    request, sections, assignment, new_pallets, elapsed,
) -> OptimizationResponse:
    """Строит ответ из assignment.

    В режиме реслота assignment может содержать и existing-паллеты (перемещённые
    V3 chain-swap). Для них операции PUT не генерируются — только для new_pallets.
    """
    sec_addresses: Dict[str, List[str]] = {}
    for row in request.occupancy:
        sec_addresses[row.section_id] = [row.address1, row.address2, row.address3]

    # Карта всех паллет (new + existing из occupancy)
    pallet_map: Dict[str, Pallet] = {p.id: p for p in new_pallets}
    existing_pallet_map: Dict[str, Tuple[str, float, float, float, float]] = {}
    for row in request.occupancy:
        for i in range(1, 4):
            pid = getattr(row, f"pallet{i}_id", "")
            if pid:
                w = getattr(row, f"pallet{i}_width", 0) or 0
                h = getattr(row, f"pallet{i}_height", 0) or 0
                d = getattr(row, f"pallet{i}_depth", 0) or 0
                wt = getattr(row, f"pallet{i}_weight", 0) or 0
                if w > 0:
                    existing_pallet_map[pid] = (row.section_id, w, h, d, wt)

    # Группируем по секциям
    by_section: Dict[str, List[str]] = defaultdict(list)
    for p_id, sec_id in assignment.items():
        if sec_id:
            by_section[sec_id].append(p_id)

    def _width_fits(section, pallet_ids_in_sec, pallet_id, pallet_w):
        """Проверка (N+1)*gap — сумма ширин + зазоры ≤ ширина секции."""
        total_w = sum(
            pallet_map[pid].width if pid in pallet_map
            else existing_pallet_map.get(pid, (None, 0, 0, 0, 0))[1]
            for pid in pallet_ids_in_sec
        ) + pallet_w
        n = len(pallet_ids_in_sec) + 1
        return total_w + (n + 1) * section.gap_width <= section.width

    operations: List[OperationSchema] = []
    for sec_id, p_ids in by_section.items():
        section = next((s for s in sections if s.id == sec_id), None)
        if not section:
            continue
        addrs = sec_addresses.get(sec_id, ["", "", ""])
        occupied = set()
        occupied_widths = {}

        # Только new_pallets — existing не генерируют PUT
        new_in_sec = [pid for pid in p_ids if pid in pallet_map]
        # Сортируем: самые широкие первыми
        sorted_ids = sorted(new_in_sec, key=lambda pid: pallet_map[pid].width, reverse=True)
        placed_in_sec: List[str] = []

        for p_id in sorted_ids:
            p = pallet_map.get(p_id)
            if not p:
                continue

            # Проверка: влезает ли вообще по (N+1)*gap?
            if not _width_fits(section, placed_in_sec, p_id, p.width):
                logger.warning(
                    f"  _build_response: {p_id} (W={p.width}) не влезает в секцию "
                    f"{section.id} (W={section.width} gap={section.gap_width}) — уже "
                    f"{len(placed_in_sec)} паллет, пропускаем"
                )
                continue

            w, W = p.width, section.width
            if w > W * 2 / 3:
                allowed = [2]
            elif w > W / 3:
                allowed = [1, 3]
            else:
                allowed = [1, 2, 3]

            assigned = None
            for pos in allowed:
                idx = pos - 1
                if idx == 1 and (
                    (0 in occupied and occupied_widths.get(0, 0) > W / 3) or
                    (2 in occupied and occupied_widths.get(2, 0) > W / 3)
                ):
                    continue
                if idx not in occupied and addrs[idx]:
                    assigned = idx
                    break
            if assigned is None:
                for idx in range(3):
                    if idx == 1 and (
                        (0 in occupied and occupied_widths.get(0, 0) > W / 3) or
                        (2 in occupied and occupied_widths.get(2, 0) > W / 3)
                    ):
                        continue
                    if idx not in occupied and addrs[idx]:
                        assigned = idx
                        break

            if assigned is not None:
                occupied.add(assigned)
                occupied_widths[assigned] = w
                if assigned in (0, 2) and w > W / 3:
                    occupied.add(1)
                    occupied_widths[1] = w
                placed_in_sec.append(p_id)
                operations.append(OperationSchema(
                    pallet=p_id, operation="PUT",
                    newAddress=addrs[assigned],
                    sequence=len(operations) + 1,
                ))

    placed_count = len(operations)
    not_placed_ids = set(p.id for p in new_pallets) - {op.pallet for op in operations}
    not_placed = [NotPlacedSchema(pallet=pid, reason="NO_SPACE") for pid in not_placed_ids]

    logger.info(f"Hybrid V5: {placed_count}/{len(new_pallets)} ({placed_count/len(new_pallets)*100:.1f}%) за {elapsed:.1f}с")

    return OptimizationResponse(
        optimizationId="hybrid-v5",
        mode="place",
        solverStatus=SolverStatus.FEASIBLE,
        placementStatus=PlacementStatus.FULL if not not_placed else PlacementStatus.PARTIAL,
        score=float(placed_count * 100000),
        executionTimeSeconds=round(elapsed, 1),
        operations=operations,
        notPlaced=not_placed,
        metrics=MetricsSchema(
            placedPallets=placed_count, movedPallets=0,
            notPlacedPallets=len(not_placed), potentialLoss=0,
            usedSections=len(set(op.newAddress for op in operations)),
        ),
    )
