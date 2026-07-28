"""Тест Hybrid V3 Solver + валидация всех 17 проверок 1С."""
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from solver.hybrid_v3 import run_hybrid_v3
from tests.test_validate_operations import _validate_operations, _print_errors

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_DIR = os.path.join(os.path.dirname(HERE), "tests", "example")


def load_occupancy(path):
    with open(path, encoding="utf-8") as f:
        return [OccupancySectionSchema(**r) for r in json.load(f)["sections"]]


def load_floor(path):
    with open(path, encoding="utf-8") as f:
        return [
            NewPalletSchema(id=f"FLOOR-{i:04d}", width=p["width"],
                          height=p["height"], depth=p["depth"], weight=p["weight"])
            for i, p in enumerate(json.load(f)["floorPallets"])
        ]


def main():
    occ = load_occupancy(os.path.join(TEST_DIR, "OccupancyS7.json"))
    floor = load_floor(os.path.join(TEST_DIR, "FloorS7.json"))
    print(f"Загружено: {len(occ)} секций, {len(floor)} паллет")

    settings = OptimizationSettingsSchema(
        allowReslot=False, maxOperations=5000, timeLimitSeconds=300,
        strictNarrowAislePlacement=True, twoStageReslot=False,
        solverType="cp_sat",
    )
    req = OptimizationRequest(
        optimizationId="v3-test", mode="place",
        occupancy=occ, newPallets=floor, settings=settings,
    )

    print("\nЗапуск Hybrid V3...")
    t0 = time.time()
    resp = run_hybrid_v3(req)
    elapsed = time.time() - t0

    placed = resp.metrics.placedPallets
    total = placed + resp.metrics.notPlacedPallets
    print(f"\n=== Результаты Hybrid V3 ===")
    print(f"  Размещено:     {placed}/{total} ({placed/total*100:.1f}%)")
    print(f"  Не размещено:  {resp.metrics.notPlacedPallets}")
    print(f"  Время:         {elapsed:.1f}с")

    # -------------------------------------------------------------------
    # Валидация всех операций через 17 проверок 1С
    # -------------------------------------------------------------------
    print(f"\n=== Валидация {len(resp.operations)} операций (17 проверок 1С) ===")
    errors, duplicates, error_reasons, virtual_state, section_by_id, \
        address_by_id, pallet_dimensions = _validate_operations(resp, occ, floor)

    error_count = len(errors)
    dup_count = len(duplicates)

    print(f"  Ошибок:           {error_count}")
    print(f"  Дублей адресов:   {dup_count}")

    if error_reasons:
        print(f"  Типы ошибок:      {dict(error_reasons)}")

    if error_count > 0 or dup_count > 0:
        _print_errors(errors, error_reasons, section_by_id, address_by_id,
                     pallet_dimensions, virtual_state)
        if duplicates:
            print(f"\n  ДУБЛИ АДРЕСОВ ({dup_count}):")
            for addr, pals in list(duplicates.items())[:10]:
                print(f"    {addr}: {pals}")
        print(f"\n  [FAIL] {error_count} ошибок, {dup_count} дублей")
    else:
        print(f"\n  [OK] Все 17 проверок пройдены, 0 ошибок, 0 дублей")

    # -------------------------------------------------------------------
    # Сравнение
    # -------------------------------------------------------------------
    print(f"\n=== Сравнение ===")
    print(f"  Hybrid V3:        {placed} ({placed/total*100:.1f}%) — {elapsed:.1f}с")
    print(f"  Hybrid V2:         3212 (94.3%) — 3.1с")
    print(f"  NumPy solver:      3167 (93.0%) — 48с")
    print(f"  CP-SAT aggregate:  3332 (97.8%) — 160-190с")
    print(f"  Ручной эталон S6:  3242 (95.2%)")


if __name__ == "__main__":
    main()
