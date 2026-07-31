"""Диагностика остатков V7 после BFD."""
import json, sys, logging, time
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, '.')
from api.schemas import *
from solver.hybrid_v7 import HybridV7Solver
from optimizer.potential import section_fits_pallet
from collections import Counter

occ = [OccupancySectionSchema(**r) for r in json.load(open('tests/example/OccupancyS7.json',encoding='utf-8'))['sections']]
floor = [NewPalletSchema(id=f'FLOOR-{i:04d}', width=p['width'], height=p['height'], depth=p['depth'], weight=p['weight']) for i,p in enumerate(json.load(open('tests/example/FloorS7.json',encoding='utf-8'))['floorPallets'])]
settings = OptimizationSettingsSchema(allowReslot=False, maxOperations=5000, timeLimitSeconds=15, strictNarrowAislePlacement=True, twoStageReslot=False, solverType='cp_sat')

t0 = time.time()
solver = HybridV7Solver(occ, floor, settings)
solver._phase_bfd_multistart()
print(f"BFD: {len(solver.placements)} placed за {time.time()-t0:.1f}с, остаток: {len(solver.new_pallets)}")

# Заполненность секций
fill = Counter()
for sid, st in solver.section_states.items():
    n = len(st.placed_pallets)
    fill[n] += 1
print("\nЗаполненность секций:")
for k in sorted(fill.keys()):
    print(f"  {k} паллет: {fill[k]} секций")

# Остаток: сколько секций реально влезает (с учётом занятости)
print(f"\nАнализ остатка ({len(solver.new_pallets)} паллет)...")
t1 = time.time()
leftover_fit = Counter()
leftover_types = Counter()
for p in solver.new_pallets:
    n = 0
    for sid, st in solver.section_states.items():
        if section_fits_pallet(st.section, st.placed_pallets, p, strict_narrow=True):
            n += 1
    leftover_fit[n] += 1
    leftover_types[(p.is_narrow, p.height, p.width, p.depth)] += 1
print(f"Анализ за {time.time()-t1:.1f}с")
print("\nОстаток — сколько секций реально влезает:")
for k in sorted(leftover_fit.keys()):
    print(f"  {k} секций: {leftover_fit[k]} паллет")
print(f"Паллет с 0 вариантов: {leftover_fit[0]}")
print(f"Паллет с >0 вариантов: {sum(v for k,v in leftover_fit.items() if k>0)}")

# Топ типов остатка
print("\nТоп типов остатка:")
for k, v in leftover_types.most_common(10):
    print(f"  narrow={k[0]} h={k[1]} w={k[2]} d={k[3]}: {v} шт")