"""Global Optimizer — оркестрация всего пайплайна оптимизации (§11 ТЗ).

Пайплайн:
1. Парсинг occupancy → Section/Address/Pallet (models/occupancy_builder.py)
2. FFD Warm Start
3. CP-SAT глобальная оптимизация (Паллета → Секция)
4. Section Optimizer (Паллета → Адрес)
5. Формирование delta-результата — только паллеты с реально изменившимся адресом

mode="place": размещаем req.newPallets, существующие движимые паллеты могут
реслотиться в пределах settings.allowReslot/maxReslotPercent.
mode="compact": новых паллет нет, реслот существующих движимых паллет всегда
разрешён (это единственная цель режима) — уплотнение склада.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Set, Tuple

from api.schemas import (
    MetricsSchema, NotPlacedSchema, OperationSchema,
    OptimizationRequest, OptimizationResponse,
    PlacementStatus, SolverStatus,
)
from models.address import Address
from models.occupancy_builder import build_warehouse_state
from models.pallet import Pallet, PalletTypeSize
from models.section import Section
from optimizer.potential import compute_potential, section_fits_pallet
from optimizer.scoring import GlobalScoreComponents, compute_global_score
from optimizer.section_optimizer import assign_addresses
from solver.config import FEASIBLE_PAIRS_THRESHOLD
from solver.cp_sat_aggregated import CPSATAggregatedSolver
from solver.cp_sat_model import CPSATSolver
from solver.feasibility import compute_feasible_pairs, count_pairs
from solver.warm_start import first_fit_decreasing

logger = logging.getLogger(__name__)


def run_optimization(req: OptimizationRequest) -> OptimizationResponse:
    """Точка входа: принимает OptimizationRequest, возвращает OptimizationResponse."""
    # Если включен двухэтапный режим — делегируем в two_stage_optimizer
    if req.settings.twoStageReslot and req.mode == "place":
        from optimizer.two_stage_optimizer import run_two_stage_optimization
        return run_two_stage_optimization(req)

    t_start = time.perf_counter()

    sections, addresses, existing_pallets = build_warehouse_state(req.occupancy)
    new_pallets = _build_new_pallets(req.newPallets) if req.mode == "place" else []

    settings = req.settings
    allow_reslot = True if req.mode == "compact" else settings.allowReslot

    logger.info(
        "run_optimization: id=%s mode=%s new=%d existing=%d sections=%d twoStageReslot=%s",
        req.optimizationId, req.mode, len(new_pallets), len(existing_pallets), len(sections),
        settings.twoStageReslot,
    )

    section_map: Dict[str, Section] = {s.id: s for s in sections}
    address_map: Dict[str, Address] = {a.id: a for a in addresses}

    existing_pallet_map = {p.id: p for p in existing_pallets}
    section_current_pallets: Dict[str, List[Pallet]] = {s.id: [] for s in sections}
    pallet_current_address: Dict[str, str] = {}
    pallet_current_section: Dict[str, str] = {}
    for addr in addresses:
        if addr.pallet_id and addr.pallet_id in existing_pallet_map:
            section_current_pallets[addr.section_id].append(existing_pallet_map[addr.pallet_id])
            pallet_current_address[addr.pallet_id] = addr.id
            pallet_current_section[addr.pallet_id] = addr.section_id

    warm_start = first_fit_decreasing(
        new_pallets=new_pallets,
        existing_pallets=existing_pallets,
        sections=sections,
        addresses=addresses,
        allow_reslot=allow_reslot,
        strict_narrow=settings.strictNarrowAislePlacement,
    )

    cp_settings = settings.model_copy(update={"allowReslot": allow_reslot})

    # Агрегированная модель (Фаза C) корректна только когда нет решений о
    # реслоте — иначе "эту существующую паллету двигать или нет" относится к
    # конкретному экземпляру, а не к типоразмеру, и агрегация её не смоделирует.
    movable_existing = [p for p in existing_pallets if p.movable]
    no_reslot_decisions = not allow_reslot or not movable_existing

    section_idx = {s.id: i for i, s in enumerate(sections)}
    feasible_preview = compute_feasible_pairs(
        pallets=new_pallets + movable_existing,
        sections=sections,
        strict_narrow=settings.strictNarrowAislePlacement,
        pallet_current_section=pallet_current_section,
        section_idx=section_idx,
    )
    total_pairs = count_pairs(feasible_preview)
    use_aggregated = no_reslot_decisions and total_pairs > FEASIBLE_PAIRS_THRESHOLD
    logger.info(
        "id=%s feasible_pairs=%d threshold=%d no_reslot_decisions=%s aggregated=%s",
        req.optimizationId, total_pairs, FEASIBLE_PAIRS_THRESHOLD, no_reslot_decisions, use_aggregated,
    )

    solver_cls = CPSATAggregatedSolver if use_aggregated else CPSATSolver

    # Strategy pattern: NumPy/LP solver при solverType="numpy"/"lp"
    if settings.solverType == "numpy":
        from solver.numpy_solver import NumpySolver
        solver_cls = NumpySolver
        logger.info("id=%s используем NumPy solver (solverType='numpy')", req.optimizationId)
    elif settings.solverType == "lp":
        from solver.lp_solver import LPSolver
        solver_cls = LPSolver
        logger.info("id=%s используем LP solver (solverType='lp')", req.optimizationId)
    elif settings.solverType in ("hybrid_v3", "hybrid-v3"):
        from solver.hybrid_v3 import run_hybrid_v3
        logger.info("id=%s используем Hybrid V3 (solverType='%s')", req.optimizationId, settings.solverType)
        return run_hybrid_v3(req)
    elif settings.solverType in ("hybrid_v5", "hybrid-v5"):
        from solver.hybrid_v5 import run_hybrid_v5
        logger.info("id=%s используем Hybrid V5 aggregate (solverType='%s')", req.optimizationId, settings.solverType)
        return run_hybrid_v5(req)

    cp_solver = solver_cls(
        sections=sections,
        new_pallets=new_pallets,
        existing_pallets=existing_pallets,
        addresses=addresses,
        settings=cp_settings,
        warm_start=warm_start,
    )
    assignment, solver_status_str, cp_score = cp_solver.solve()
    logger.info(
        "CP-SAT solve: id=%s model=%s status=%s score=%.0f branches=%d conflicts=%d wallTime=%.2fs",
        req.optimizationId, solver_cls.__name__, solver_status_str, cp_score,
        cp_solver.solver_branches, cp_solver.solver_conflicts, cp_solver.solver_wall_time,
    )

    # Только реально переместившиеся (сменившие секцию) движимые паллеты идут на
    # переадресацию — иначе assign_addresses выбрал бы им новый адрес даже когда
    # allowReslot=false / секция не поменялась (это выглядело бы как MOVE без переезда).
    movable_for_addressing = [
        ep for ep in existing_pallets
        if ep.movable and assignment.get(ep.id) and assignment.get(ep.id) != pallet_current_section.get(ep.id)
    ]
    movable_for_addressing_ids = {ep.id for ep in movable_for_addressing}
    all_for_addressing = new_pallets + movable_for_addressing

    virtual_section_pallets: Dict[str, List[Pallet]] = {s.id: [] for s in sections}
    for ep in existing_pallets:
        if ep.id in movable_for_addressing_ids:
            continue
        assigned_sec = assignment.get(ep.id)
        if assigned_sec:
            virtual_section_pallets[assigned_sec].append(ep)

    address_assignment = assign_addresses(
        pallets=all_for_addressing,
        section_assignment=assignment,
        section_map=section_map,
        address_map=address_map,
    )

    for p in all_for_addressing:
        sec_id = assignment.get(p.id)
        addr_id = address_assignment.get(p.id)
        if sec_id and addr_id:
            virtual_section_pallets.setdefault(sec_id, []).append(p)

    operations: List[OperationSchema] = []
    not_placed: List[NotPlacedSchema] = []
    used_sections_set: Set[str] = set()
    moved_count = 0
    seq = 1

    # Существующие движимые паллеты — только реальные изменения адреса (delta)
    for ep in existing_pallets:
        if not ep.movable:
            continue
        new_sec_id = assignment.get(ep.id)
        new_addr_id = address_assignment.get(ep.id)
        old_addr_id = pallet_current_address.get(ep.id)

        if not new_sec_id or not new_addr_id or new_addr_id == old_addr_id:
            continue

        moved_count += 1
        used_sections_set.add(new_sec_id)
        operations.append(OperationSchema(
            pallet=ep.id,
            operation="MOVE",
            oldAddress=old_addr_id,
            newAddress=new_addr_id,
            sequence=seq,
        ))
        seq += 1

    # Новые паллеты
    placed_new = 0
    for np in new_pallets:
        new_sec_id = assignment.get(np.id)
        new_addr_id = address_assignment.get(np.id)
        if new_sec_id and new_addr_id:
            placed_new += 1
            used_sections_set.add(new_sec_id)
            operations.append(OperationSchema(
                pallet=np.id,
                operation="PUT",
                oldAddress=None,
                newAddress=new_addr_id,
                sequence=seq,
            ))
            seq += 1
        else:
            reason, details = _determine_not_placed_reason(np, sections, virtual_section_pallets, settings.strictNarrowAislePlacement)
            not_placed.append(NotPlacedSchema(pallet=np.id, reason=reason, details=details))

    total_potential_loss = 0
    for sec_id in used_sections_set:
        sec = section_map[sec_id]
        before_pals = section_current_pallets.get(sec_id, [])
        after_pals = virtual_section_pallets.get(sec_id, [])
        before_pot = compute_potential(sec, before_pals, new_pallets)
        after_pot = compute_potential(sec, after_pals, [])
        total_potential_loss += max(0, before_pot - after_pot)

    not_placed_count = len(not_placed)

    metrics = MetricsSchema(
        placedPallets=placed_new,
        notPlacedPallets=not_placed_count,
        movedPallets=moved_count,
        potentialLoss=total_potential_loss,
        usedSections=len(used_sections_set),
    )

    score_components = GlobalScoreComponents(
        placed_pallets=placed_new,
        section_moves=moved_count,
        address_moves=0,
        potential_loss=total_potential_loss,
        unused_space=0,
        used_sections=len(used_sections_set),
    )
    final_score = compute_global_score(score_components)

    solver_status = SolverStatus(solver_status_str)
    if not new_pallets:
        placement_status = PlacementStatus.FULL
    elif not_placed_count == 0:
        placement_status = PlacementStatus.FULL
    elif placed_new > 0:
        placement_status = PlacementStatus.PARTIAL
    else:
        placement_status = PlacementStatus.NONE

    t_end = time.perf_counter()

    return OptimizationResponse(
        optimizationId=req.optimizationId,
        mode=req.mode,
        solverStatus=solver_status,
        placementStatus=placement_status,
        score=final_score,
        executionTimeSeconds=round(t_end - t_start, 3),
        operations=operations,
        notPlaced=not_placed,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _build_new_pallets(pallet_schemas) -> List[Pallet]:
    result = []
    for p in pallet_schemas:
        result.append(Pallet(
            id=p.id,
            type_size=PalletTypeSize(width=p.width, height=p.height, depth=p.depth, weight=p.weight),
            access_level=p.accessLevel,
        ))
    return result


def _determine_not_placed_reason(
    pallet: Pallet,
    sections: List[Section],
    section_pallets: Dict[str, List[Pallet]],
    strict_narrow: bool = True,
) -> Tuple[str, dict]:
    """Определяет причину, по которой паллета не размещена (§12 ТЗ)."""
    checked = 0
    available = 0
    reasons: Set[str] = set()

    for sec in sections:
        checked += 1
        current = section_pallets.get(sec.id, [])
        if strict_narrow and pallet.is_narrow and not sec.narrow_aisle:
            reasons.add("NARROW_AISLE_MISMATCH")
            continue
        if pallet.height > sec.height:
            reasons.add("HEIGHT_LIMIT")
            continue
        if pallet.depth > sec.depth:
            reasons.add("DEPTH_LIMIT")
            continue
        if pallet.weight > sec.max_lift_weight:
            reasons.add("LIFT_LIMIT")
            continue
        if pallet.width > sec.eff_max_width or pallet.depth > sec.eff_max_depth:
            reasons.add("MAX_PALLET_SIZE_LIMIT")
            continue
        total_weight = sum(p.weight for p in current) + pallet.weight
        if total_weight > sec.max_weight:
            reasons.add("WEIGHT_LIMIT")
            continue
        if not section_fits_pallet(sec, current, pallet, strict_narrow):
            reasons.add("NO_SPACE")
            continue
        available += 1

    if available > 0:
        primary_reason = "RESLOT_LIMIT"
    elif "NARROW_AISLE_MISMATCH" in reasons:
        primary_reason = "NARROW_AISLE_MISMATCH"
    elif "HEIGHT_LIMIT" in reasons:
        primary_reason = "HEIGHT_LIMIT"
    elif "DEPTH_LIMIT" in reasons:
        primary_reason = "DEPTH_LIMIT"
    elif "LIFT_LIMIT" in reasons:
        primary_reason = "LIFT_LIMIT"
    elif "MAX_PALLET_SIZE_LIMIT" in reasons:
        primary_reason = "MAX_PALLET_SIZE_LIMIT"
    elif "WEIGHT_LIMIT" in reasons:
        primary_reason = "WEIGHT_LIMIT"
    else:
        primary_reason = "NO_SPACE"

    return primary_reason, {"checkedSections": checked, "availableSections": available}
