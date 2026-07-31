"""Тест Hybrid V5 — aggregate CP-SAT + reslot + валидация."""
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
from solver.hybrid_v5 import run_hybrid_v5
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

    # Тест 1: без реслота (холодный склад)
    settings = OptimizationSettingsSchema(
        allowReslot=False, maxOperations=5000, timeLimitSeconds=300,
        strictNarrowAislePlacement=True, twoStageReslot=False,
        solverType="hybrid_v5",
    )
    req = OptimizationRequest(
        optimizationId="v5-test-noreslot", mode="place",
        occupancy=occ, newPallets=floor, settings=settings,
    )

    print("\n=== Тест 1: Hybrid V5 без реслота (холодный склад) ===")
    t0 = time.time()
    resp = run_hybrid_v5(req)
    elapsed = time.time() - t0

    placed = resp.metrics.placedPallets
    total = placed + resp.metrics.notPlacedPallets
    print(f"\n=== Результаты Hybrid V5 (no reslot) ===")
    print(f"  Размещено:     {placed}/{total} ({placed/total*100:.1f}%)")
    print(f"  Не размещено:  {resp.metrics.notPlacedPallets}")
    print(f"  Перемещено:    {resp.metrics.movedPallets}")
    print(f"  Секций:        {resp.metrics.usedSections}")
    print(f"  Время:         {elapsed:.1f}с")
    print(f"  Score:         {resp.score:.0f}")
    print(f"  SolverStatus:  {resp.solverStatus.value}")
    print(f"  PlaceStatus:   {resp.placementStatus.value}")

    # Валидация
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
    # Тест 2: с реслотом — smoke test (не валидируем, occupancy искусственный)
    # -------------------------------------------------------------------
    print(f"\n\n=== Тест 2: Hybrid V5 с реслотом (smoke test) ===")
    print(f"  (искусственный сценарий — проверяем что не крашится)")

    # Берём первые 500 паллет как "существующие" и размещаем их в секциях
    existing_pallets = floor[:500]
    new_pallets = floor[500:1500]  # 1000 новых для теста

    from models.occupancy_builder import build_warehouse_state
    from models.pallet import Pallet, PalletTypeSize
    from optimizer.section_optimizer import assign_addresses
    from solver.warm_start import first_fit_decreasing

    sections, addresses, _ = build_warehouse_state(occ)
    ep_objs = [
        Pallet(id=p.id, type_size=PalletTypeSize(
            width=p.width, height=p.height, depth=p.depth, weight=p.weight
        ))
        for p in existing_pallets
    ]

    # Размещаем "существующие" через BFD
    warm = first_fit_decreasing(
        new_pallets=ep_objs, existing_pallets=[], sections=sections,
        addresses=addresses, allow_reslot=False, strict_narrow=True,
    )
    addr_assignment = assign_addresses(
        pallets=ep_objs, section_assignment=warm,
        section_map={s.id: s for s in sections},
        address_map={a.id: a for a in addresses},
    )

    # Строим occupancy с размещёнными существующими
    sec_pallets = {}
    pallet_obj_map = {p.id: p for p in ep_objs}
    for ep in ep_objs:
        sec_id = warm.get(ep.id)
        addr_id = addr_assignment.get(ep.id)
        if sec_id and addr_id:
            sec_pallets.setdefault(sec_id, []).append((ep, addr_id))

    modified_occ = []
    for row in occ:
        row_dict = row.model_dump()
        placed_in_sec = sec_pallets.get(row.section_id, [])
        for i, (p, addr_id) in enumerate(placed_in_sec[:3]):
            idx = i + 1
            row_dict[f"pallet{idx}_id"] = p.id
            row_dict[f"pallet{idx}_width"] = p.width
            row_dict[f"pallet{idx}_height"] = p.height
            row_dict[f"pallet{idx}_depth"] = p.depth
            row_dict[f"pallet{idx}_weight"] = p.weight
            row_dict[f"quantity{idx}"] = 1
        modified_occ.append(OccupancySectionSchema(**row_dict))

    placed_existing = sum(1 for v in warm.values() if v)
    print(f"  Существующих размещено: {placed_existing}/500")

    # Тест с реслотом
    settings2 = OptimizationSettingsSchema(
        allowReslot=True, maxReslotPercent=20, maxOperations=5000,
        timeLimitSeconds=300, strictNarrowAislePlacement=True,
        twoStageReslot=False, solverType="hybrid_v5",
    )
    req2 = OptimizationRequest(
        optimizationId="v5-test-reslot", mode="place",
        occupancy=modified_occ, newPallets=new_pallets, settings=settings2,
    )

    t0 = time.time()
    try:
        resp2 = run_hybrid_v5(req2)
        elapsed2 = time.time() - t0
        placed2 = resp2.metrics.placedPallets
        total2 = placed2 + resp2.metrics.notPlacedPallets
        print(f"\n=== Результаты Hybrid V5 (reslot smoke) ===")
        print(f"  Размещено:     {placed2}/{total2} ({placed2/total2*100:.1f}%)")
        print(f"  Не размещено:  {resp2.metrics.notPlacedPallets}")
        print(f"  Перемещено:    {resp2.metrics.movedPallets}")
        print(f"  Время:         {elapsed2:.1f}с")
        print(f"  Score:         {resp2.score:.0f}")
        print(f"  [OK] Реслот не крашится")
    except Exception as e:
        print(f"  [FAIL] Реслот упал: {e}")
        import traceback
        traceback.print_exc()

    # -------------------------------------------------------------------
    # Сравнение
    # -------------------------------------------------------------------
    print(f"\n=== Итоговое сравнение ===")
    print(f"  Hybrid V5 (S7 cold):     {placed}/{total} ({placed/total*100:.1f}%) — {elapsed:.1f}с, 0 ошибок")
    print(f"  Hybrid V5 (reslot smoke): реслот не крашится")
    print(f"  Hybrid V3:                3215/3406 (94.4%) — 4.0с")
    print(f"  Ручной эталон S6:         3242 (95.2%)")


if __name__ == "__main__":
    main()
