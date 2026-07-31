"""Compaction-only reslot: переупаковка заполненного склада для максимизации свободных секций.

Использует V3 chain-swap механизм, но с другой целевой функцией:
вместо "разместить leftover" — "освободить секцию".

Алгоритм:
1. Загружаем состояние после V3 cold start (3215 паллет, склад заполнен)
2. Для каждой секции с 1 паллетой (W>=1600 в 2300mm):
   - Пробуем "вытеснить" паллету через chain-swap в 2700mm секцию где уже есть узкая
   - Если цепочка найдена → выполняем → секция освобождается
3. Для каждой секции с 2 узкими паллетами (2×W<=900):
   - Пробуем переместить одну в секцию где 1 широкая + есть место
   - Если цепочка найдена → секция становится 1-pallet с большим free_w
4. Замеряем: сколько секций освобождено, сколько free_w>=1700 создано
"""
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationSettingsSchema, OperationSchema,
    NotPlacedSchema, OptimizationResponse, PlacementStatus, SolverStatus, MetricsSchema,
)
from solver.hybrid_v3 import HybridV3Solver
from tests.test_validate_operations import _validate_operations

GAP = 50.0


def main():
    HERE = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR = os.path.dirname(HERE)
    TEST_DIR = os.path.join(BASE_DIR, "tests", "example")

    with open(os.path.join(TEST_DIR, "OccupancyS7.json"), encoding="utf-8") as f:
        occ = [OccupancySectionSchema(**r) for r in json.load(f)["sections"]]
    with open(os.path.join(TEST_DIR, "FloorS7.json"), encoding="utf-8") as f:
        floor = [
            NewPalletSchema(
                id=f"FLOOR-{i:04d}",
                width=p["width"], height=p["height"],
                depth=p["depth"], weight=p["weight"],
            )
            for i, p in enumerate(json.load(f)["floorPallets"])
        ]

    # =========================================================================
    # Phase 1: V3 cold start — заполняем склад
    # =========================================================================
    settings = OptimizationSettingsSchema(
        allowReslot=False, maxReslotPercent=0, maxOperations=5000,
        timeLimitSeconds=60, strictNarrowAislePlacement=True,
        twoStageReslot=False, solverType="hybrid_v3",
    )

    t0 = time.time()
    solver = HybridV3Solver(occupancy=occ, new_pallets=floor, settings=settings)
    solver._phase_bfd()
    solver._phase_chain_swap()
    solver._phase_micro_cpsat()
    operations1, _ = solver._assign_addresses()
    placed1 = len([op for op in operations1 if op.operation == "PUT"])
    print(f"Phase 1 (V3 cold start): {placed1} placed, {len(solver.new_pallets)} leftovers")

    # =========================================================================
    # Анализ состояния после Phase 1
    # =========================================================================
    def analyze():
        empty = 0
        single = 0
        single_wide = 0  # 1 pallet W>=1600 in 2300mm
        two_narrow = 0   # 2 pallets both W<=900
        liquid = 0       # free_w >= 1700
        medium = 0       # 1000 <= free_w < 1700

        for sec_id, state in solver.section_states.items():
            n = len(state.placed_pallets)
            fw = state.free_width
            if n == 0:
                empty += 1
            elif n == 1:
                single += 1
                p = state.placed_pallets[0]
                if p.width >= 1600 and state.section.width == 2300:
                    single_wide += 1
            elif n == 2:
                if all(p.width <= 900 for p in state.placed_pallets):
                    two_narrow += 1

            if fw >= 1700:
                liquid += 1
            elif fw >= 1000:
                medium += 1

        return {
            "total": len(solver.section_states),
            "empty": empty,
            "single": single,
            "single_wide_2300": single_wide,
            "two_narrow": two_narrow,
            "liquid": liquid,
            "medium": medium,
        }

    before = analyze()
    print(f"\nДо compaction:")
    print(f"  Секций всего:       {before['total']}")
    print(f"  Пустых:             {before['empty']}")
    print(f"  С 1 паллетой:       {before['single']} (из них W>=1600 в 2300mm: {before['single_wide_2300']})")
    print(f"  С 2 узкими (<=900): {before['two_narrow']}")
    print(f"  free_w >= 1700:     {before['liquid']}")
    print(f"  free_w >= 1000:     {before['medium']}")

    # =========================================================================
    # Phase 2: Compaction через chain-swap
    # =========================================================================
    # Включаем реслот: разрешаем двигать ВСЕ размещённые паллеты (не только existing)
    solver._max_reslot_moves = 999999  # без лимита
    # Помечаем ВСЕ placed pallets как movable (включая новые)
    all_placed_ids = set()
    for state in solver.section_states.values():
        for p in state.placed_pallets:
            all_placed_ids.add(p.id)
    # Добавляем их в movable_existing_ids (chain-swap проверяет этот сет)
    solver.movable_existing_ids.update(all_placed_ids)

    compaction_ops = []
    freed_sections = []

    # Стратегия 1: консолидация single-pallet 2300mm секций
    # Для каждой секции с 1 широкой паллетой в 2300mm:
    #   пытаемся переместить эту паллету в 2700mm секцию где есть место
    print(f"\nCompaction: поиск цепочек для single-pallet секций...")

    single_secs = [
        (sec_id, state)
        for sec_id, state in solver.section_states.items()
        if len(state.placed_pallets) == 1
        and state.section.width == 2300
    ]
    print(f"  Single-pallet 2300mm секций: {len(single_secs)}")

    chains_found = 0
    for sec_id, state in single_secs:
        pallet = state.placed_pallets[0]

        # Ищем куда переместить эту паллету
        compat = solver.compatible.get(pallet.id, [])
        for target_sec_id in compat:
            if target_sec_id == sec_id:
                continue
            target_state = solver.section_states[target_sec_id]
            if target_state.free_count <= 0:
                continue
            if target_state.section.width != 2700:
                continue  # предпочитаем 2700mm
            if target_state.free_width < pallet.width + GAP:
                continue  # не влезает

            # Нашли целевую секцию — создаём цепочку
            # Перемещаем паллету из sec_id → target_sec_id
            chain = [
                ("remove", pallet.id, sec_id),
                ("place", pallet.id, target_sec_id),
            ]
            solver._execute_chain(chain)
            chains_found += 1
            freed_sections.append(sec_id)
            break  # одна паллета размещена

    print(f"  Найдено и выполнено цепочек: {chains_found}")
    print(f"  Освобождено секций: {len(freed_sections)}")

    # Стратегия 2: перенос узкой паллеты из 2-pallet секции в секцию с широкой
    print(f"\nCompaction: поиск цепочек для 2-narrow секций...")
    two_narrow_secs = [
        (sec_id, state)
        for sec_id, state in solver.section_states.items()
        if len(state.placed_pallets) == 2
        and all(p.width <= 900 for p in state.placed_pallets)
    ]
    print(f"  2-narrow секций: {len(two_narrow_secs)}")

    chains2 = 0
    for sec_id, state in two_narrow_secs:
        # Пробуем переместить одну узкую паллету в секцию с широкой (W>=1600) + место
        for pallet in state.placed_pallets:
            compat = solver.compatible.get(pallet.id, [])
            for target_sec_id in compat:
                if target_sec_id == sec_id:
                    continue
                target_state = solver.section_states[target_sec_id]
                if target_state.free_count <= 0:
                    continue
                if target_state.section.width != state.section.width:
                    continue

                # В целевой секции уже есть широкая паллета?
                has_wide = any(p.width >= 1600 for p in target_state.placed_pallets)
                if not has_wide:
                    continue

                if target_state.free_width < pallet.width + GAP:
                    continue

                chain = [
                    ("remove", pallet.id, sec_id),
                    ("place", pallet.id, target_sec_id),
                ]
                solver._execute_chain(chain)
                chains2 += 1

                # После перемещения, source секция стала 1-pallet
                # Проверяем: теперь в ней free_w >= 1700?
                new_free = state.free_width + pallet.width + GAP
                if new_free >= 1700:
                    freed_sections.append(sec_id)
                break
            else:
                continue
            break

    print(f"  Найдено и выполнено цепочек: {chains2}")

    # =========================================================================
    # Анализ после compaction
    # =========================================================================
    after = analyze()
    print(f"\nПосле compaction:")
    print(f"  Секций всего:       {after['total']}")
    print(f"  Пустых:             {after['empty']}")
    print(f"  С 1 паллетой:       {after['single']} (из них W>=1600 в 2300mm: {after['single_wide_2300']})")
    print(f"  С 2 узкими (<=900): {after['two_narrow']}")
    print(f"  free_w >= 1700:     {after['liquid']} (Δ={after['liquid'] - before['liquid']})")
    print(f"  free_w >= 1000:     {after['medium']} (Δ={after['medium'] - before['medium']})")

    print(f"\n  Освобождено секций: {len(freed_sections)}")

    # =========================================================================
    # Phase 3: повторное размещение leftover'ов
    # =========================================================================
    print(f"\nPhase 3: повторное размещение {len(solver.new_pallets)} leftover'ов...")

    # Строим modified occupancy
    addr_pallet = {}
    for row in occ:
        for i in range(1, 4):
            addr = getattr(row, f"address{i}", "")
            p_id = getattr(row, f"pallet{i}_id", "")
            if addr and p_id and p_id != "00000000-0000-0000-0000-000000000000":
                addr_pallet[addr] = p_id
    for op in operations1:
        if op.operation == "PUT" and op.newAddress:
            addr_pallet[op.newAddress] = op.pallet

    pallet_data = {}
    for row in occ:
        for i in range(1, 4):
            p_id = getattr(row, f"pallet{i}_id", "")
            if p_id and p_id != "00000000-0000-0000-0000-000000000000":
                pallet_data[p_id] = {
                    "code": getattr(row, f"pallet{i}_code", p_id),
                    "width": getattr(row, f"pallet{i}_width", 0),
                    "height": getattr(row, f"pallet{i}_height", 0),
                    "depth": getattr(row, f"pallet{i}_depth", 0),
                    "weight": getattr(row, f"pallet{i}_weight", 0),
                }
    for p in floor:
        pallet_data[p.id] = {
            "code": p.id, "width": p.width,
            "height": p.height, "depth": p.depth, "weight": p.weight,
        }

    # Обновляем addr_pallet из section_states (после compaction)
    # Строим заново: для каждой секции, распределяем паллеты по адресам
    # Упрощённо: назначем адреса последовательно
    modified_occ = []
    for row in occ:
        d = row.model_dump()
        for i in range(1, 4):
            d[f"pallet{i}_id"] = ""
            d[f"pallet{i}_code"] = ""
            d[f"pallet{i}_width"] = 0
            d[f"pallet{i}_height"] = 0
            d[f"pallet{i}_depth"] = 0
            d[f"pallet{i}_weight"] = 0
            d[f"quantity{i}"] = 0
            d[f"blocked{i}"] = 0

        state = solver.section_states.get(row.section_id)
        if state and state.placed_pallets:
            addrs = [row.address1, row.address2, row.address3]
            for idx, pallet in enumerate(state.placed_pallets[:3]):
                i = idx + 1
                p_info = pallet_data.get(pallet.id, {})
                d[f"pallet{i}_id"] = pallet.id
                d[f"pallet{i}_code"] = p_info.get("code", pallet.id)
                d[f"pallet{i}_width"] = p_info.get("width", 0)
                d[f"pallet{i}_height"] = p_info.get("height", 0)
                d[f"pallet{i}_depth"] = p_info.get("depth", 0)
                d[f"pallet{i}_weight"] = p_info.get("weight", 0)
                d[f"quantity{i}"] = 1

        modified_occ.append(OccupancySectionSchema(**d))

    # V3 на модифицированном складе
    settings3 = OptimizationSettingsSchema(
        allowReslot=False, maxReslotPercent=0, maxOperations=5000,
        timeLimitSeconds=60, strictNarrowAislePlacement=True,
        twoStageReslot=False, solverType="hybrid_v3",
    )
    solver3 = HybridV3Solver(
        occupancy=modified_occ,
        new_pallets=solver.new_pallets,
        settings=settings3,
    )
    solver3._phase_bfd()
    solver3._phase_chain_swap()
    operations3, _ = solver3._assign_addresses()
    placed3 = len([op for op in operations3 if op.operation == "PUT"])
    leftovers3 = len(solver3.new_pallets)

    print(f"  Дополнительно размещено: {placed3}")
    print(f"  Осталось неразмещённых:  {leftovers3}")
    print(f"  Итого размещено:         {placed1 + placed3}/{len(floor)}")

    # Строим финальные операции (упрощённо: только Phase 1 + Phase 3, без MOVEs)
    all_ops = list(operations1) + list(operations3)
    for i, op in enumerate(all_ops):
        op.sequence = i + 1

    elapsed = time.time() - t0
    print(f"\n  Время: {elapsed:.1f}с")
    print(f"  Δ от V3: {'+' if placed3 > 0 else ''}{placed3}")

    # Валидация
    resp = OptimizationResponse(
        optimizationId="compaction-test",
        mode="place",
        solverStatus=SolverStatus.FEASIBLE,
        placementStatus=PlacementStatus.PARTIAL,
        score=float((placed1 + placed3) * 100000),
        executionTimeSeconds=round(elapsed, 1),
        operations=all_ops,
        notPlaced=[NotPlacedSchema(pallet=p.id, reason="NO_SPACE") for p in solver3.new_pallets],
        metrics=MetricsSchema(
            placedPallets=placed1 + placed3,
            movedPallets=0,
            notPlacedPallets=leftovers3,
            potentialLoss=0,
            usedSections=len(set(op.newAddress for op in all_ops)),
        ),
    )
    errors, dups, reasons, vs, sbi, abi, pd = _validate_operations(resp, occ, floor)
    if len(errors) == 0 and len(dups) == 0:
        print(f"  [OK] 0 ошибок валидации")
    else:
        print(f"  [FAIL] {len(errors)} ошибок, {len(dups)} дублей")


if __name__ == "__main__":
    main()
