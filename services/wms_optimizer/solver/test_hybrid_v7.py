"""Тест Hybrid V7 Solver + валидация всех 17 проверок 1С."""
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
from solver.hybrid_v7 import run_hybrid_v7
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
        allowReslot=False, maxOperations=5000, timeLimitSeconds=15,
        strictNarrowAislePlacement=True, twoStageReslot=False,
        solverType="cp_sat",
    )
    req = OptimizationRequest(
        optimizationId="v7-test", mode="place",
        occupancy=occ, newPallets=floor, settings=settings,
    )

    print("\nЗапуск Hybrid V7...")
    t0 = time.time()
    resp = run_hybrid_v7(req)
    elapsed = time.time() - t0

    placed = resp.metrics.placedPallets
    total = placed + resp.metrics.notPlacedPallets
    print(f"\n=== Результаты Hybrid V7 ===")
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
    print(f"  Hybrid V7:        {placed} ({placed/total*100:.1f}%) — {elapsed:.1f}с")
    print(f"  Hybrid V6:         3215 (94.4%) — 4.6с")
    print(f"  Hybrid V5:         3220 (94.5%) — 114с")
    print(f"  CP-SAT aggregated: 3239 (95.0%) — 188с")
    print(f"  Ручной эталон S6:  3242 (95.2%)")

    # -------------------------------------------------------------------
    # Проверка цели
    # -------------------------------------------------------------------
    print(f"\n=== Проверка цели ===")
    goal_placed = 3242
    goal_time = 15.0
    placed_ok = placed >= goal_placed
    time_ok = elapsed <= goal_time
    print(f"  ≥{goal_placed} паллет: {'✅ ДА' if placed_ok else '❌ НЕТ'} ({placed})")
    print(f"  ≤{goal_time}с:         {'✅ ДА' if time_ok else '❌ НЕТ'} ({elapsed:.1f}с)")
    if placed_ok and time_ok:
        print(f"\n  🎯 ЦЕЛЬ ДОСТИГНУТА!")
    else:
        print(f"\n  ⚠️ Цель не достигнута — нужна доработка")


if __name__ == "__main__":
    main()