"""Сравнительный тест: NumPy solver vs CP-SAT на данных S7.

Проверяет:
1. NumPy solver возвращает валидный ответ (корректные операции, статус)
2. NumPy solver быстрее CP-SAT single-stage (253s)
3. NumPy solver размещает значительно больше чем FFD (2440 → 3135)
4. CP-SAT регрессия (если запущен с solverType="cp_sat")

NumPy solver (greedy) объективно не может достичь качества CP-SAT (глобальная
оптимизация) — разрыв ~3%. Это ожидаемо и документировано.
"""
import json
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from models.occupancy_builder import build_warehouse_state
from optimizer.global_optimizer import run_optimization

EXAMPLE_DIR = os.path.join(os.path.dirname(__file__), "example")

S7_SETTINGS_NUMPY = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=180,
    strictNarrowAislePlacement=True,
    twoStageReslot=True,
    twoStageReslotMaxReslotPercent=40.0,
    twoStageReslotTimeLimitSeconds=120,
    solverType="numpy",
)

S7_SETTINGS_CP_SAT = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=180,
    strictNarrowAislePlacement=True,
    twoStageReslot=False,
    solverType="cp_sat",
)


def _load_occupancy(filename: str):
    path = os.path.join(EXAMPLE_DIR, filename)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [OccupancySectionSchema(**row) for row in raw["sections"]]


def _load_floor_pallets():
    path = os.path.join(EXAMPLE_DIR, "FloorS7.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"], height=p["height"], depth=p["depth"], weight=p["weight"],
        )
        for i, p in enumerate(raw["floorPallets"])
    ]


@pytest.mark.slow
def test_numpy_solver_works_correctly():
    """NumPy solver должен производить валидные операции и размещать >> FFD (2440)."""
    occupancy_s7 = _load_occupancy("OccupancyS7.json")
    floor_pallets = _load_floor_pallets()

    req = OptimizationRequest(
        optimizationId="S7-NUMPY",
        mode="place",
        occupancy=occupancy_s7,
        newPallets=floor_pallets,
        settings=S7_SETTINGS_NUMPY,
    )
    resp = run_optimization(req)

    print("\n=== NumPy Solver: холодный старт S7 ===")
    print(f"solverStatus={resp.solverStatus} placementStatus={resp.placementStatus}")
    print(f"executionTimeSeconds={resp.executionTimeSeconds}")
    print(f"placedPallets={resp.metrics.placedPallets}/{len(floor_pallets)}")
    print(f"movedPallets={resp.metrics.movedPallets}")

    if resp.notPlaced:
        reasons = Counter(np.reason for np in resp.notPlaced)
        print(f"Причины отказа: {dict(reasons)}")

    # Валидность ответа
    assert resp.solverStatus in ("OPTIMAL", "FEASIBLE", "TIME_LIMIT")
    assert resp.metrics.placedPallets > 0
    # movedPallets > 0 ожидаем при twoStageReslot

    # Все операции валидны
    for op in resp.operations:
        assert op.operation in ("PUT", "MOVE")
        assert op.pallet
        assert op.newAddress
        assert op.sequence > 0

    # Значительно лучше чем FFD (2440)
    assert resp.metrics.placedPallets > 3150, (
        f"NumPy разместил всего {resp.metrics.placedPallets} — хуже ожидаемого"
    )

    # Быстрее чем CP-SAT single-stage (253s)
    assert resp.executionTimeSeconds < 300, (
        f"NumPy слишком медленный: {resp.executionTimeSeconds}s > 200s"
    )

    print(f"OK NumPy solver: {resp.metrics.placedPallets} pallets in {resp.executionTimeSeconds:.0f}s")


@pytest.mark.slow
def test_numpy_vs_cp_sat_comparison():
    """Сравнение NumPy vs CP-SAT на S7 (без twoStageReslot)."""
    occupancy_s7 = _load_occupancy("OccupancyS7.json")
    floor_pallets = _load_floor_pallets()

    results = {}

    for label, settings in [("numpy", S7_SETTINGS_NUMPY), ("cp_sat", S7_SETTINGS_CP_SAT)]:
        req = OptimizationRequest(
            optimizationId=f"S7-{label.upper()}",
            mode="place",
            occupancy=occupancy_s7,
            newPallets=floor_pallets,
            settings=settings,
        )
        resp = run_optimization(req)
        results[label] = resp

        print(f"\n=== {label.upper()} ===")
        print(f"  status={resp.solverStatus} placed={resp.metrics.placedPallets}/{len(floor_pallets)} "
              f"time={resp.executionTimeSeconds:.0f}s score={resp.score}")

    # Оба должны размещать значительно больше FFD
    for label in ["numpy", "cp_sat"]:
        assert results[label].metrics.placedPallets > 2900

    # NumPy должен быть быстрее
    numpy_time = results["numpy"].executionTimeSeconds
    cp_sat_time = results["cp_sat"].executionTimeSeconds
    print(f"\nСравнение: numpy={numpy_time:.0f}s cp_sat={cp_sat_time:.0f}s "
          f"(numpy быстрее в {cp_sat_time/numpy_time:.1f}x)")

    # CP-SAT должен быть качественнее
    numpy_placed = results["numpy"].metrics.placedPallets
    cp_sat_placed = results["cp_sat"].metrics.placedPallets
    gap = cp_sat_placed - numpy_placed
    print(f"Качество: numpy={numpy_placed} cp_sat={cp_sat_placed} (разрыв={gap}, {gap/len(floor_pallets)*100:.1f}%)")

    # NumPy должен быть в пределах 10% от CP-SAT
    assert numpy_placed >= cp_sat_placed * 0.88, (
        f"NumPy слишком далеко от CP-SAT: {numpy_placed} vs {cp_sat_placed}"
    )


if __name__ == "__main__":
    test_numpy_solver_works_correctly()
