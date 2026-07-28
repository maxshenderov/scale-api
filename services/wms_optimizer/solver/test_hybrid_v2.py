"""Тест Hybrid V2 Solver на данных S7 (3406 паллет, холодный старт).

Запуск: python solver/test_hybrid_v2.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema,
    OccupancySectionSchema,
    OptimizationRequest,
    OptimizationSettingsSchema,
)
from solver.hybrid_v2 import run_hybrid_v2

# ---------------------------------------------------------------------------
# Загрузка данных
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_DIR = os.path.join(os.path.dirname(HERE), "tests", "example")


def load_occupancy(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [OccupancySectionSchema(**row) for row in raw["sections"]]


def load_floor_pallets(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"],
            height=p["height"],
            depth=p["depth"],
            weight=p["weight"],
        )
        for i, p in enumerate(raw["floorPallets"])
    ]


def main():
    # Загружаем S7 (холодный старт)
    occ_path = os.path.join(TEST_DIR, "OccupancyS7.json")
    floor_path = os.path.join(TEST_DIR, "FloorS7.json")

    print("=" * 60)
    print("Hybrid V2 Solver — тест на S7 (3406 паллет)")
    print("=" * 60)

    print("\n[1] Загрузка данных...")
    occupancy = load_occupancy(occ_path)
    floor_pallets = load_floor_pallets(floor_path)
    print(f"  Секций: {len(occupancy)}")
    print(f"  Паллет с пола: {len(floor_pallets)}")

    print("\n[2] Запуск Hybrid V2...")
    settings = OptimizationSettingsSchema(
        allowReslot=False,
        maxOperations=5000,
        timeLimitSeconds=300,
        strictNarrowAislePlacement=True,
        twoStageReslot=False,
        solverType="cp_sat",
    )

    req = OptimizationRequest(
        optimizationId="hybrid-v2-test",
        mode="place",
        occupancy=occupancy,
        newPallets=floor_pallets,
        settings=settings,
    )

    t0 = time.time()
    resp = run_hybrid_v2(req)
    elapsed = time.time() - t0

    print(f"\n[3] Результаты:")
    total = resp.metrics.placedPallets + resp.metrics.notPlacedPallets
    print(f"  Статус:           {resp.solverStatus}")
    print(f"  Размещено:        {resp.metrics.placedPallets}/{total}")
    print(f"  % размещения:     {resp.metrics.placedPallets/total*100:.1f}%")
    print(f"  Не размещено:     {resp.metrics.notPlacedPallets}")
    print(f"  Операций:         {len(resp.operations)}")
    print(f"  Время (wall):     {elapsed:.1f}с")
    print(f"  Время (solver):   {resp.executionTimeSeconds:.1f}с")

    if resp.notPlaced:
        from collections import Counter
        reasons = Counter(np.reason for np in resp.notPlaced)
        print(f"  Причины:          {dict(reasons)}")

    # Сравнение с эталоном
    ref_path = os.path.join(TEST_DIR, "OccupancyS6Standard.json")
    if os.path.exists(ref_path):
        ref_occ = load_occupancy(ref_path)
        from models.occupancy_builder import build_warehouse_state
        _, _, ref_pallets = build_warehouse_state(ref_occ)
        delta = resp.metrics.placedPallets - len(ref_pallets)
        print(f"\n  Ручной эталон S6: {len(ref_pallets)}")
        print(f"  Разница:          {delta:+d}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
