"""Diagnostic v2: analyze ALL sections by pallet count and free width after V3."""
import json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.schemas import NewPalletSchema, OccupancySectionSchema, OptimizationSettingsSchema
from solver.hybrid_v3 import HybridV3Solver

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.join(BASE_DIR, "tests", "example")

with open(os.path.join(TEST_DIR, "OccupancyS7.json"), encoding="utf-8") as f:
    occ = [OccupancySectionSchema(**r) for r in json.load(f)["sections"]]
with open(os.path.join(TEST_DIR, "FloorS7.json"), encoding="utf-8") as f:
    floor = [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"], height=p["height"],
            depth=p["depth"], weight=p["weight"],
        )
        for i, p in enumerate(json.load(f)["floorPallets"])
    ]

settings = OptimizationSettingsSchema(
    allowReslot=False, maxReslotPercent=0, maxOperations=5000,
    timeLimitSeconds=60, strictNarrowAislePlacement=True,
    twoStageReslot=False, solverType="hybrid_v3",
)
solver = HybridV3Solver(occupancy=occ, new_pallets=floor, settings=settings)
solver._phase_bfd()
solver._phase_chain_swap()
solver._phase_micro_cpsat()

GAP = 50

# Analyze all sections
by_count = defaultdict(list)
for sec_id, state in solver.section_states.items():
    n = len(state.placed_pallets)
    widths = [p.width for p in state.placed_pallets]
    by_count[n].append({
        "sec_w": state.section.width,
        "free_w": state.free_width,
        "free_n": state.free_count,
        "p_widths": widths,
        "p_total_w": sum(widths),
    })

print(f"Total sections: {len(solver.section_states)}")
print()

for n in sorted(by_count.keys()):
    items = by_count[n]
    print(f"=== {n} pallet(s) per section: {len(items)} sections ===")

    # Group by section width
    by_w = defaultdict(list)
    for item in items:
        by_w[item["sec_w"]].append(item)

    for sw, sitems in sorted(by_w.items()):
        free_ws = [s["free_w"] for s in sitems]
        avg_free = sum(free_ws) / len(free_ws)
        min_free = min(free_ws)
        max_free = max(free_ws)

        # For each, what's the widest pallet that could fit?
        # max_pallet_w = free_w - GAP (need 1 more gap for the new pallet)
        max_fittable = [fw - GAP for fw in free_ws]

        print(f"  W={sw}: {len(sitems)} sections, "
              f"free_w avg={avg_free:.0f} min={min_free:.0f} max={max_free:.0f}, "
              f"max_fittable_pallet avg={sum(max_fittable)/len(max_fittable):.0f}")

    print()

# Key question: any sections with free_w >= 1700 (can fit W>=1600)?
print("=== Sections with free_width >= 1700 (can fit W>=1600 leftover) ===")
count = 0
for sec_id, state in solver.section_states.items():
    if state.free_width >= 1700:
        count += 1
        widths = [p.width for p in state.placed_pallets]
        print(f"  W={state.section.width} n={len(state.placed_pallets)} "
              f"free_w={state.free_width:.0f} free_n={state.free_count} "
              f"pallet_widths={widths}")
print(f"  Total: {count} sections")

# Sections with free_w >= 1000 (the user's liquidity threshold)
print()
print("=== Sections with free_width >= 1000 ===")
count = 0
for sec_id, state in solver.section_states.items():
    if state.free_width >= 1000 and state.free_count >= 1:
        count += 1
print(f"  Total: {count} sections with free_w>=1000 and free_n>=1")

# What about sections with 2 pallets where both are narrow (<=900)?
print()
print("=== 2700mm sections with 2 pallets, both W<=900 ===")
count = 0
for sec_id, state in solver.section_states.items():
    if state.section.width != 2700:
        continue
    if len(state.placed_pallets) != 2:
        continue
    if all(p.width <= 900 for p in state.placed_pallets):
        count += 1
        widths = [p.width for p in state.placed_pallets]
        if count <= 5:
            print(f"  free_w={state.free_width:.0f} free_n={state.free_count} widths={widths}")
print(f"  Total: {count} sections")
