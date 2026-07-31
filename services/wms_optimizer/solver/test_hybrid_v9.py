"""Тест Hybrid V9 — greedy section-first, двухпроходный: холодный старт → реслот + валидация."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from solver.hybrid_v9 import run_hybrid_v9
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


def build_occupancy_from_ops(occ, pallet_map, operations):
    """Строит occupancy, где размещённые паллеты занимают правильные адреса."""
    addr_pallet = {}
    for op in operations:
        if op.operation == "PUT":
            addr_pallet[op.newAddress] = op.pallet

    modified_occ = []
    for row in occ:
        row_dict = row.model_dump()
        for i in range(1, 4):
            for field in ("id", "code", "width", "height", "depth", "weight"):
                row_dict[f"pallet{i}_{field}"] = "" if field in ("id", "code") else 0
            row_dict[f"quantity{i}"] = 0

        addrs = [row.address1, row.address2, row.address3]
        for i, addr in enumerate(addrs):
            idx = i + 1
            p_id = addr_pallet.get(addr, "")
            if p_id:
                p = pallet_map.get(p_id)
                if p:
                    row_dict[f"pallet{idx}_id"] = p.id
                    row_dict[f"pallet{idx}_code"] = p.id
                    row_dict[f"pallet{idx}_width"] = p.width
                    row_dict[f"pallet{idx}_height"] = p.height
                    row_dict[f"pallet{idx}_depth"] = p.depth
                    row_dict[f"pallet{idx}_weight"] = p.weight
                    row_dict[f"quantity{idx}"] = 1
        modified_occ.append(OccupancySectionSchema(**row_dict))
    return modified_occ


def main():
    occ = load_occupancy(os.path.join(TEST_DIR, "OccupancyS7.json"))
    floor = load_floor(os.path.join(TEST_DIR, "FloorS7.json"))
    pallet_map = {p.id: p for p in floor}
    total_pallets = len(floor)
    print(f"Загружено: {len(occ)} секций, {total_pallets} паллет")

    # =====================================================================
    # ПРОХОД 1: Холодный старт (без реслота)
    # =====================================================================
    print(f"\n{'='*60}")
    print(f"ПРОХОД 1: Холодный старт (allowReslot=False)")
    print(f"{'='*60}")

    settings1 = OptimizationSettingsSchema(
        allowReslot=False, maxOperations=5000, timeLimitSeconds=15,
        strictNarrowAislePlacement=True, twoStageReslot=False,
        solverType="cp_sat",
    )
    req1 = OptimizationRequest(
        optimizationId="v9-pass1", mode="place",
        occupancy=occ, newPallets=floor, settings=settings1,
    )

    t0 = time.time()
    resp1 = run_hybrid_v9(req1)
    elapsed1 = time.time() - t0

    placed1 = resp1.metrics.placedPallets
    not_placed1 = resp1.metrics.notPlacedPallets
    moved1 = resp1.metrics.movedPallets
    print(f"\n  Размещено:     {placed1}/{total_pallets} ({placed1/total_pallets*100:.1f}%)")
    print(f"  Не размещено:  {not_placed1}")
    print(f"  Перемещено:    {moved1}")
    print(f"  Время:         {elapsed1:.1f}с")

    print(f"\n  Валидация {len(resp1.operations)} операций...")
    errors1, dups1, reasons1, vs1, sbi1, abi1, pd1 = _validate_operations(resp1, occ, floor)
    err1 = len(errors1)
    dup1 = len(dups1)
    if err1 == 0 and dup1 == 0:
        print(f"  [OK] Проход 1: 0 ошибок, 0 дублей")
    else:
        print(f"  [FAIL] Проход 1: {err1} ошибок, {dup1} дублей")
        _print_errors(errors1, reasons1, sbi1, abi1, pd1, vs1)

    # =====================================================================
    # ПРОХОД 2: Реслот
    # =====================================================================
    if not_placed1 == 0:
        print(f"\n  Все паллеты размещены в проходе 1 — реслот не нужен.")
    else:
        print(f"\n{'='*60}")
        print(f"ПРОХОД 2: Реслот {not_placed1} leftover-паллет")
        print(f"{'='*60}")

        occ2 = build_occupancy_from_ops(occ, pallet_map, resp1.operations)
        print(f"  Occupancy pass2: {len(occ2)} секций "
              f"(с {len([op for op in resp1.operations if op.operation == 'PUT'])} existing паллетами)")

        placed_ids1 = {op.pallet for op in resp1.operations if op.operation == "PUT"}
        leftovers = [p for p in floor if p.id not in placed_ids1]

        if not leftovers:
            print(f"  Нет leftover паллет — все размещены.")
        else:
            print(f"  Leftover паллет: {len(leftovers)}")
            print(f"  Existing паллет в occupancy: {len(placed_ids1)}")

            settings2 = OptimizationSettingsSchema(
                allowReslot=True, maxReslotPercent=10, maxOperations=5000,
                timeLimitSeconds=15, strictNarrowAislePlacement=True,
                twoStageReslot=False, solverType="cp_sat",
            )
            req2 = OptimizationRequest(
                optimizationId="v9-pass2", mode="place",
                occupancy=occ2, newPallets=leftovers, settings=settings2,
            )

            t0_2 = time.time()
            resp2 = run_hybrid_v9(req2)
            elapsed2 = time.time() - t0_2

            placed2 = resp2.metrics.placedPallets
            not_placed2 = resp2.metrics.notPlacedPallets
            moved2 = resp2.metrics.movedPallets
            total_placed = placed1 + placed2

            print(f"\n  Размещено pass2: {placed2}/{len(leftovers)} ({placed2/len(leftovers)*100:.1f}%)")
            print(f"  Не размещено:     {not_placed2}")
            print(f"  Перемещено (MOVE): {moved2}")
            print(f"  Время pass2:      {elapsed2:.1f}с")
            print(f"  ИТОГО размещено:  {total_placed}/{total_pallets} ({total_placed/total_pallets*100:.1f}%)")

            print(f"\n  Валидация {len(resp2.operations)} операций...")
            errors2, dups2, reasons2, vs2, sbi2, abi2, pd2 = _validate_operations(resp2, occ2, leftovers)
            err2 = len(errors2)
            dup2 = len(dups2)
            if err2 == 0 and dup2 == 0:
                print(f"  [OK] Проход 2: 0 ошибок, 0 дублей")
            else:
                print(f"  [FAIL] Проход 2: {err2} ошибок, {dup2} дублей")
                if reasons2:
                    print(f"  Типы ошибок: {dict(reasons2)}")
                _print_errors(errors2, reasons2, sbi2, abi2, pd2, vs2)

    # =====================================================================
    # ИТОГО
    # =====================================================================
    print(f"\n{'='*60}")
    print(f"ИТОГО")
    print(f"{'='*60}")
    total_placed_final = placed1 + (placed2 if not_placed1 > 0 else 0)
    print(f"  Pass 1 (cold):     {placed1}/{total_pallets} ({placed1/total_pallets*100:.1f}%) — {elapsed1:.1f}с")
    if not_placed1 > 0:
        print(f"  Pass 2 (reslot):   +{placed2} = {total_placed_final}/{total_pallets} ({total_placed_final/total_pallets*100:.1f}%) — {elapsed2:.1f}с")
    print(f"  V3 baseline:       3215/3406 (94.4%) — 4.0с")
    print(f"  V5 baseline:       3236/3406 (95.0%) — 368с")
    print(f"  V6 cold:           3215/3406 (94.4%) — 8-16с")
    print(f"  Target:            >=3242 (95.2%)")


if __name__ == "__main__":
    main()
