"""Диагностика: какие секции подходят остаткам по чистым габаритам."""
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

solver = HybridV7Solver(occ, floor, settings)
solver._phase_bfd_multistart()
print(f"BFD: {len(solver.placements)} placed, остаток: {len(solver.new_pallets)}")

# Для каждого типа остатка: сколько секций подходит по ЧИСТЫМ габаритам (без занятости)
print("\n=== Анализ по типам остатка ===")
leftover_by_type = Counter()
for p in solver.new_pallets:
    leftover_by_type[(p.is_narrow, p.height, p.width, p.depth)] += 1

for typ, count in leftover_by_type.most_common(10):
    is_narrow, h, w, d = typ
    # Чистые габариты (без занятости)
    fits_clean = 0
    fits_with_occupancy = 0
    for sid, st in solver.section_states.items():
        sec = st.section
        # Проверка габаритов без занятости
        if is_narrow and settings.strictNarrowAislePlacement and not sec.narrow_aisle:
            continue
        if h > sec.height:
            continue
        if d > sec.depth:
            continue
        if w > sec.eff_max_width:
            continue
        if d > sec.eff_max_depth:
            continue
        fits_clean += 1
        # С учётом занятости
        if section_fits_pallet(sec, st.placed_pallets, solver.new_pallets[0], strict_narrow=True):
            fits_with_occupancy += 1
    print(f"  narrow={is_narrow} h={h} w={w} d={d}: {count} шт | секций по габаритам: {fits_clean} | с занятостью: {fits_with_occupancy}")

# Проверим: какие секции имеют свободное место, но остаток не влезает
print("\n=== Секции с свободным местом (free_count>0) ===")
can_fit_something = 0
for sid, st in solver.section_states.items():
    if st.free_count <= 0:
        continue
    # Проверим влезет ли хоть одна паллета из остатка
    for p in solver.new_pallets:
        if section_fits_pallet(st.section, st.placed_pallets, p, strict_narrow=True):
            can_fit_something += 1
            break
print(f"Секций с free_count>0: {sum(1 for st in solver.section_states.values() if st.free_count>0)}")
print(f"Из них влезает хоть один остаток: {can_fit_something}")

# Проверим: сколько секций с 2 паллетами имеют свободное место по ширине
print("\n=== Секции с 2 паллетами — анализ ширины ===")
two_pallet_secs = []
for sid, st in solver.section_states.items():
    if len(st.placed_pallets) == 2 and st.free_count > 0:
        two_pallet_secs.append((sid, st))
print(f"Секций с 2 паллетами и free_count>0: {len(two_pallet_secs)}")
# Минимальная ширина остатка
min_leftover_w = min(p.width for p in solver.new_pallets)
print(f"Минимальная ширина остатка: {min_leftover_w}")
# Сколько секций с 2 паллетами могут вместить минимальный остаток
can_fit_min = 0
for sid, st in two_pallet_secs:
    if st.free_width >= min_leftover_w + st.gap_width:
        can_fit_min += 1
print(f"Из них влезает минимальный остаток (w={min_leftover_w}): {can_fit_min}")