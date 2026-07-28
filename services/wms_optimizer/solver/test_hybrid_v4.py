"""Тест Hybrid V4 (LNS) + валидация 17 проверок 1С."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from solver.hybrid_v4 import run_hybrid_v4
from tests.test_validate_operations import _validate_operations

HERE = os.path.dirname(os.path.abspath(__file__))
TD = os.path.join(os.path.dirname(HERE), "tests", "example")


def load_occ(p):
    with open(p, encoding="utf-8") as f:
        return [OccupancySectionSchema(**r) for r in json.load(f)["sections"]]


def load_floor(p):
    with open(p, encoding="utf-8") as f:
        return [NewPalletSchema(id=f"FLOOR-{i:04d}", width=r["width"],
                height=r["height"], depth=r["depth"], weight=r["weight"])
                for i, r in enumerate(json.load(f)["floorPallets"])]


occ = load_occ(os.path.join(TD, "OccupancyS7.json"))
floor = load_floor(os.path.join(TD, "FloorS7.json"))
print(f"Загружено: {len(occ)} секций, {len(floor)} паллет")

settings = OptimizationSettingsSchema(
    allowReslot=False, maxOperations=5000, timeLimitSeconds=300,
    strictNarrowAislePlacement=True, twoStageReslot=False, solverType="cp_sat",
)
req = OptimizationRequest(optimizationId="v4", mode="place",
                          occupancy=occ, newPallets=floor, settings=settings)

print("\nЗапуск Hybrid V4 (LNS)...")
t0 = time.time()
resp = run_hybrid_v4(req)
elapsed = time.time() - t0

ops = len(resp.operations)
np_count = resp.metrics.notPlacedPallets
total = ops + np_count
print(f"\n=== Результаты ===")
print(f"  Операций:     {ops}")
print(f"  Не размещено: {np_count}")
print(f"  Всего:        {total}")
print(f"  Размещение:   {ops}/{total} ({ops/total*100:.1f}%)")
print(f"  Время:        {elapsed:.1f}с")

# Валидация
print(f"\n=== Валидация {ops} операций ===")
errors, dups, reasons, _, _, _, _ = _validate_operations(resp, occ, floor)
print(f"  Ошибок:        {len(errors)}")
print(f"  Дублей:        {len(dups)}")
if reasons:
    print(f"  Типы:          {dict(reasons)}")
if errors:
    print(f"  [FAIL] {len(errors)} ошибок")
else:
    print(f"  [OK] Все 17 проверок пройдены!")

print(f"\n=== Сравнение ===")
print(f"  Hybrid V4 (LNS):   {ops} ({ops/total*100:.1f}%) — {elapsed:.1f}с")
print(f"  Hybrid V3:          2886 (84.7%) — 4.0с")
print(f"  NumPy solver:       3167 (93.0%) — 48с")
print(f"  CP-SAT aggregate:   3332 (97.8%) — 160-190с")
print(f"  Ручной эталон S6:   3242 (95.2%)")
