"""Тест Hybrid V12 — rating-guided multi-start BFD + валидация."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from solver.hybrid_v12 import run_hybrid_v12
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
    total_pallets = len(floor)
    print(f"Загружено: {len(occ)} секций, {total_pallets} паллет")

    settings = OptimizationSettingsSchema(
        allowReslot=False, maxReslotPercent=0, maxOperations=5000,
        timeLimitSeconds=60, strictNarrowAislePlacement=True,
        twoStageReslot=False, solverType="hybrid_v3",
    )
    req = OptimizationRequest(
        optimizationId="v12-test", mode="place",
        occupancy=occ, newPallets=floor, settings=settings,
    )

    print(f"\n{'='*60}")
    print(f"Hybrid V12: Rating-guided multi-start BFD")
    print(f"{'='*60}")

    t0 = time.time()
    resp = run_hybrid_v12(req)
    elapsed = time.time() - t0

    placed = resp.metrics.placedPallets
    not_placed = resp.metrics.notPlacedPallets
    moved = resp.metrics.movedPallets
    print(f"\n  Размещено:     {placed}/{total_pallets} ({placed/total_pallets*100:.1f}%)")
    print(f"  Не размещено:  {not_placed}")
    print(f"  Перемещено:    {moved}")
    print(f"  Время:         {elapsed:.1f}с")
    print(f"  Операций:      {len(resp.operations)}")

    # Show BFD strategy comparison
    puts = [op for op in resp.operations if op.operation == "PUT"]

    # Single-pallet sections diagnostic
    from collections import defaultdict
    sec_pallets = defaultdict(list)
    for op in puts:
        sec_pallets[op.newAddress].append(op.pallet)
    singles = sum(1 for pals in sec_pallets.values() if len(pals) == 1)

    # Валидация
    print(f"\n  Валидация {len(resp.operations)} операций...")
    errors, dups, reasons, vs, sbi, abi, pd = _validate_operations(resp, occ, floor)
    err_count = len(errors)
    dup_count = len(dups)
    if err_count == 0 and dup_count == 0:
        print(f"  [OK] 0 ошибок, 0 дублей")
    else:
        print(f"  [FAIL] {err_count} ошибок, {dup_count} дублей")
        if reasons:
            print(f"  Типы ошибок: {dict(reasons)}")
        _print_errors(errors, reasons, sbi, abi, pd, vs)
        if dups:
            print(f"\n  ДУБЛИ АДРЕСОВ ({dup_count}):")
            for addr, pals in list(dups.items())[:10]:
                print(f"    {addr}: {pals}")

    # =====================================================================
    # ИТОГО
    # =====================================================================
    delta = placed - 3215
    print(f"\n{'='*60}")
    print(f"ИТОГО")
    print(f"{'='*60}")
    print(f"  V12:             {placed}/{total_pallets} ({placed/total_pallets*100:.1f}%) — {elapsed:.1f}с, {err_count} ошибок")
    print(f"  Δ от V3:         {'+' if delta >= 0 else ''}{delta}")
    print(f"  V3 baseline:     3215/3406 (94.4%) — 4.0с, 0 ошибок")
    print(f"  Target:          >=3242 (95.2%)")


if __name__ == "__main__":
    main()
