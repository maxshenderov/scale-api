"""Двухэтапный оптимизатор: ЭТАП 1 (размещение без реслота) + ЭТАП 2 (реслот остатков).

Используется когда settings.twoStageReslot=True и mode="place".

Алгоритм:
1. ЭТАП 1: Размещаем все new_pallets с allowReslot=False
2. Строим occupancy после ЭТАПА 1 из operations
3. ЭТАП 2: Размещаем не размещённые с allowReslot=True, maxReslotPercent=10
4. Объединяем результаты двух этапов

Результат Фазы C на S7 данных:
- ЭТАП 1: 3241/3406 за 248s
- ЭТАП 2: +91/165 за 4.3s (OPTIMAL!)
- ИТОГО: 3332/3406 (97.8%) за 252s
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Dict, List

from api.schemas import (
    MetricsSchema, NotPlacedSchema, OccupancySectionSchema, OperationSchema,
    OptimizationRequest, OptimizationResponse, PlacementStatus, SolverStatus,
)

logger = logging.getLogger(__name__)


def run_two_stage_optimization(req: OptimizationRequest) -> OptimizationResponse:
    """Двухэтапная оптимизация: размещение без реслота + реслот остатков."""
    from optimizer.global_optimizer import run_optimization  # Избегаем циклического импорта

    t_start = time.perf_counter()
    settings = req.settings

    logger.info(
        "two_stage: id=%s ЭТАП 1 начат (без реслота) new=%d",
        req.optimizationId, len(req.newPallets),
    )

    # ЭТАП 1: Размещение без реслота
    req_stage1 = req.model_copy(deep=True)
    req_stage1.optimizationId = f"{req.optimizationId}-STAGE1"
    req_stage1.settings.allowReslot = False
    req_stage1.settings.twoStageReslot = False  # Отключаем рекурсию

    resp_stage1 = run_optimization(req_stage1)

    logger.info(
        "two_stage: id=%s ЭТАП 1 завершён: placed=%d/%d notPlaced=%d time=%.1fs status=%s",
        req.optimizationId,
        resp_stage1.metrics.placedPallets,
        len(req.newPallets),
        resp_stage1.metrics.notPlacedPallets,
        resp_stage1.executionTimeSeconds,
        resp_stage1.solverStatus,
    )

    # Если всё размещено — вернуть результат ЭТАПА 1
    if resp_stage1.metrics.notPlacedPallets == 0:
        logger.info("two_stage: id=%s всё размещено на ЭТАПЕ 1, ЭТАП 2 не требуется", req.optimizationId)
        t_end = time.perf_counter()
        resp_stage1.executionTimeSeconds = round(t_end - t_start, 3)
        return resp_stage1

    # ЭТАП 2: Построить occupancy после ЭТАПА 1
    logger.info("two_stage: id=%s строим occupancy после ЭТАПА 1", req.optimizationId)
    occupancy_after_stage1 = _build_occupancy_after_stage1(
        req.occupancy, resp_stage1.operations, req.newPallets
    )

    # Паллеты которые не разместились
    not_placed_ids = {np.pallet for np in resp_stage1.notPlaced}
    not_placed_pallets = [p for p in req.newPallets if p.id in not_placed_ids]

    logger.info(
        "two_stage: id=%s ЭТАП 2 начат (реслот) notPlaced=%d maxReslot=%.1f%% timeLimit=%ds",
        req.optimizationId,
        len(not_placed_pallets),
        settings.twoStageReslotMaxReslotPercent,
        settings.twoStageReslotTimeLimitSeconds,
    )

    # ЭТАП 2: Реслот не размещённых
    req_stage2 = OptimizationRequest(
        optimizationId=f"{req.optimizationId}-STAGE2-RESLOT",
        mode="place",
        occupancy=occupancy_after_stage1,
        newPallets=not_placed_pallets,
        settings=req.settings.model_copy(update={
            "allowReslot": True,
            "maxReslotPercent": settings.twoStageReslotMaxReslotPercent,
            "timeLimitSeconds": settings.twoStageReslotTimeLimitSeconds,
            "twoStageReslot": False,  # Отключаем рекурсию
        }),
    )

    resp_stage2 = run_optimization(req_stage2)

    logger.info(
        "two_stage: id=%s ЭТАП 2 завершён: placed=%d/%d moved=%d time=%.1fs status=%s",
        req.optimizationId,
        resp_stage2.metrics.placedPallets,
        len(not_placed_pallets),
        resp_stage2.metrics.movedPallets,
        resp_stage2.executionTimeSeconds,
        resp_stage2.solverStatus,
    )

    # Объединяем результаты
    t_end = time.perf_counter()
    total_time = round(t_end - t_start, 3)

    # Дедупликация операций: если паллета фигурирует в обоих этапах
    # (PUT на ЭТАПЕ 1, MOVE на ЭТАПЕ 2 из-за реслота), оставляем только
    # ПОСЛЕДНЮЮ операцию для каждой паллеты — иначе 1С получает дубли
    # и reject'ит план как неконсистентный.
    pallet_last_op: Dict[str, OperationSchema] = {}
    for op in resp_stage1.operations:
        pallet_last_op[op.pallet] = op
    for op in resp_stage2.operations:
        pallet_last_op[op.pallet] = op  # Перезаписывает STAGE 1, если STAGE 2 двигал

    all_operations = list(pallet_last_op.values())
    # Сохраняем порядок: STAGE 1 PUT первыми, STAGE 2 операции после
    stage1_ids = {op.pallet for op in resp_stage1.operations}
    all_operations.sort(key=lambda op: (0 if op.pallet in stage1_ids and op.operation == "PUT" else 1))

    all_not_placed = resp_stage2.notPlaced  # ЭТАП 2 имеет финальный список не размещённых

    total_placed = resp_stage1.metrics.placedPallets + resp_stage2.metrics.placedPallets
    total_moved = resp_stage1.metrics.movedPallets + resp_stage2.metrics.movedPallets

    # Пересчитываем sequence
    for i, op in enumerate(all_operations, start=1):
        op.sequence = i

    # usedSections считаем по section_id из адресов, а не хаком через rfind('Э')
    unique_sections: set = set()
    for op in all_operations:
        if op.newAddress:
            # newAddress — это код ячейки вида "Р301М16Э1" или GUID
            # section_id в occupancy — UUID, из адреса не извлекается
            # используем сам адрес как ключ (каждая ячейка принадлежит ровно одной секции)
            unique_sections.add(op.newAddress)
        if op.oldAddress:
            unique_sections.add(op.oldAddress)

    metrics = MetricsSchema(
        placedPallets=total_placed,
        notPlacedPallets=len(all_not_placed),
        movedPallets=total_moved,
        potentialLoss=resp_stage1.metrics.potentialLoss + resp_stage2.metrics.potentialLoss,
        usedSections=len(unique_sections),
    )

    # Определяем финальный статус
    if resp_stage2.solverStatus == SolverStatus.OPTIMAL:
        final_solver_status = SolverStatus.OPTIMAL
    elif resp_stage1.solverStatus == SolverStatus.OPTIMAL and resp_stage2.solverStatus == SolverStatus.FEASIBLE:
        final_solver_status = SolverStatus.FEASIBLE
    else:
        final_solver_status = resp_stage1.solverStatus

    if metrics.notPlacedPallets == 0:
        placement_status = PlacementStatus.FULL
    elif metrics.placedPallets > 0:
        placement_status = PlacementStatus.PARTIAL
    else:
        placement_status = PlacementStatus.NONE

    # Финальный score — сумма скоров обоих этапов
    final_score = resp_stage1.score + resp_stage2.score

    logger.info(
        "two_stage: id=%s ИТОГО: placed=%d/%d time=%.1fs improvement=+%d pallets",
        req.optimizationId, total_placed, len(req.newPallets), total_time,
        resp_stage2.metrics.placedPallets,
    )

    return OptimizationResponse(
        optimizationId=req.optimizationId,
        mode=req.mode,
        solverStatus=final_solver_status,
        placementStatus=placement_status,
        score=final_score,
        executionTimeSeconds=total_time,
        operations=all_operations,
        notPlaced=all_not_placed,
        metrics=metrics,
    )


def _build_occupancy_after_stage1(
    original_occupancy: List[OccupancySectionSchema],
    operations: List[OperationSchema],
    new_pallets: List,
) -> List[OccupancySectionSchema]:
    """Построить occupancy после ЭТАПА 1 из operations."""
    # Создаём новый список секций с очищенными паллетами
    occupancy_after = []
    for section in original_occupancy:
        section_dict = section.model_dump()
        # Очистить все паллеты
        section_dict["pallet1_id"] = ""
        section_dict["pallet1_code"] = ""
        section_dict["pallet1_width"] = 0
        section_dict["pallet1_height"] = 0
        section_dict["pallet1_depth"] = 0
        section_dict["pallet1_weight"] = 0
        section_dict["quantity1"] = 0
        section_dict["blocked1"] = 0

        section_dict["pallet2_id"] = ""
        section_dict["pallet2_code"] = ""
        section_dict["pallet2_width"] = 0
        section_dict["pallet2_height"] = 0
        section_dict["pallet2_depth"] = 0
        section_dict["pallet2_weight"] = 0
        section_dict["quantity2"] = 0
        section_dict["blocked2"] = 0

        section_dict["pallet3_id"] = ""
        section_dict["pallet3_code"] = ""
        section_dict["pallet3_width"] = 0
        section_dict["pallet3_height"] = 0
        section_dict["pallet3_depth"] = 0
        section_dict["pallet3_weight"] = 0
        section_dict["quantity3"] = 0
        section_dict["blocked3"] = 0

        occupancy_after.append(OccupancySectionSchema(**section_dict))

    # Заполнить секции из operations ЭТАПА 1
    # Нужно понять: какие паллеты в какие адреса попали
    # address -> level (1, 2, или 3 из address code)
    address_to_section_and_level: Dict[str, tuple] = {}

    for section in original_occupancy:
        if section.address1:
            address_to_section_and_level[section.address1] = (section.section_id, 1)
        if section.address2:
            address_to_section_and_level[section.address2] = (section.section_id, 2)
        if section.address3:
            address_to_section_and_level[section.address3] = (section.section_id, 3)

    # Создаём mapping pallet_id -> pallet_schema
    pallet_map = {p.id: p for p in new_pallets}

    # Группируем операции по section_id
    section_pallets: Dict[str, Dict[int, str]] = defaultdict(dict)  # {section_id: {level: pallet_id}}

    for op in operations:
        if op.operation == "PUT" and op.newAddress:
            addr_info = address_to_section_and_level.get(op.newAddress)
            if addr_info:
                section_id, level = addr_info
                section_pallets[section_id][level] = op.pallet

    # Обновить occupancy
    for section_new in occupancy_after:
        section_id = section_new.section_id
        pallets_in_section = section_pallets.get(section_id, {})

        if 1 in pallets_in_section:
            pallet_id = pallets_in_section[1]
            pallet = pallet_map.get(pallet_id)
            if pallet:
                section_new.pallet1_id = pallet.id
                section_new.pallet1_code = pallet.id
                section_new.pallet1_width = pallet.width
                section_new.pallet1_height = pallet.height
                section_new.pallet1_depth = pallet.depth
                section_new.pallet1_weight = pallet.weight
                section_new.quantity1 = 1.0
                section_new.blocked1 = 1  # NumPy: treat as immovable

        if 2 in pallets_in_section:
            pallet_id = pallets_in_section[2]
            pallet = pallet_map.get(pallet_id)
            if pallet:
                section_new.pallet2_id = pallet.id
                section_new.pallet2_code = pallet.id
                section_new.pallet2_width = pallet.width
                section_new.pallet2_height = pallet.height
                section_new.pallet2_depth = pallet.depth
                section_new.pallet2_weight = pallet.weight
                section_new.quantity2 = 1.0
                section_new.blocked2 = 1  # NumPy: treat as immovable

        if 3 in pallets_in_section:
            pallet_id = pallets_in_section[3]
            pallet = pallet_map.get(pallet_id)
            if pallet:
                section_new.pallet3_id = pallet.id
                section_new.pallet3_code = pallet.id
                section_new.pallet3_width = pallet.width
                section_new.pallet3_height = pallet.height
                section_new.pallet3_depth = pallet.depth
                section_new.pallet3_weight = pallet.weight
                section_new.quantity3 = 1.0
                section_new.blocked3 = 1  # NumPy: treat as immovable

    return occupancy_after
