"""Интеграционный тест на реальном снимке склада (tests/example/).

Фикстуры: TestOccupancy.json (1390 секций, 3048 существующих паллет),
TestFloor.json (358 паллет на полу, ожидающих размещения).

mode="place" с allowReslot=true и maxReslotPercent=100 решает обе задачи
одним прогоном CP-SAT: движимые существующие паллеты реслотятся для
уплотнения склада (metrics.movedPallets), а освободившееся место используется
для размещения паллет с пола (metrics.placedPallets). Раздельные прогоны
mode="compact" → mode="place" не подойдут — уплотнение первого прогона не
попадёт во входные данные второго, и его эффект потеряется.

ВНИМАНИЕ: медленный тест — полная CP-SAT модель на ~1400 секциях и ~3400
паллетах, несколько минут по времени (лимит solverа 180с + оверхед на
построение модели). Не входит в быстрый прогон `pytest tests/ -v`.
Запуск отдельно: pytest tests/test_real_warehouse_scenario.py -v -s
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from models.occupancy_builder import build_warehouse_state
from optimizer.global_optimizer import run_optimization

EXAMPLE_DIR = os.path.join(os.path.dirname(__file__), "example")

# "Максимальные параметры" — полный реслот без ограничений, большой лимит
# операций (не должен резать план раньше самого solverа) и щедрый time limit.
MAX_SETTINGS = OptimizationSettingsSchema(
    allowReslot=True,
    maxReslotPercent=100,
    maxOperations=5000,
    timeLimitSeconds=180,
)


def _load_occupancy():
    path = os.path.join(EXAMPLE_DIR, "TestOccupancy.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [OccupancySectionSchema(**row) for row in raw["sections"]]


def _load_floor_pallets():
    path = os.path.join(EXAMPLE_DIR, "TestFloor.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"], height=p["height"], depth=p["depth"], weight=p["weight"],
        )
        for i, p in enumerate(raw["floorPallets"])
    ]


def test_compact_and_place_from_floor_on_real_warehouse():
    """Одним прогоном mode=place: уплотнение склада + размещение паллет с пола."""
    occupancy = _load_occupancy()
    floor_pallets = _load_floor_pallets()

    req = OptimizationRequest(
        optimizationId="REAL-WAREHOUSE-001",
        mode="place",
        occupancy=occupancy,
        newPallets=floor_pallets,
        settings=MAX_SETTINGS,
    )
    resp = run_optimization(req)

    _, _, existing_pallets = build_warehouse_state(occupancy)

    print("\n=== Уплотнение + размещение с пола (реальный склад) ===")
    print(f"solverStatus={resp.solverStatus} placementStatus={resp.placementStatus} score={resp.score}")
    print(f"Секций всего: {len(occupancy)}, существующих паллет: {len(existing_pallets)}")
    print(f"Паллет с пола: {len(floor_pallets)}")
    print("--- Уплотнение ---")
    print(f"movedPallets (реслот существующих): {resp.metrics.movedPallets}")
    print(f"usedSections: {resp.metrics.usedSections}")
    print(f"potentialLoss: {resp.metrics.potentialLoss}")
    print("--- Размещение с пола ---")
    print(f"placedPallets: {resp.metrics.placedPallets}/{len(floor_pallets)}")
    print(f"notPlacedPallets: {resp.metrics.notPlacedPallets}")

    if resp.notPlaced:
        reasons = Counter(np.reason for np in resp.notPlaced)
        print(f"Причины отказа (notPlaced): {dict(reasons)}")

    assert resp.solverStatus in ("OPTIMAL", "FEASIBLE", "TIME_LIMIT")
    assert resp.metrics.placedPallets + resp.metrics.notPlacedPallets == len(floor_pallets)
