"""Diagnostic: analyze single-pallet sections after V3 cold start."""
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

# Collect single-pallet sections
singles = []
for sec_id, state in solver.section_states.items():
    if len(state.placed_pallets) == 1:
        p = state.placed_pallets[0]
        singles.append({
            "sec_id": sec_id,
            "sec_w": state.section.width,
            "sec_h": state.section.height,
            "sec_d": state.section.depth,
            "p_w": p.width,
            "p_h": p.height,
            "p_d": p.depth,
            "p_id": p.id,
            "p_is_new": p.id.startswith("FLOOR-"),
        })

print(f"Single-pallet sections: {len(singles)}")
print()

# Group by section width
by_width = defaultdict(list)
for s in singles:
    by_width[s["sec_w"]].append(s)
for w, items in sorted(by_width.items()):
    print(f"  Section W={w}: {len(items)} sections")

print()

# Show examples
print("Examples (first 10):")
for s in singles[:10]:
    new_flag = "NEW" if s["p_is_new"] else "EXISTING"
    print(f"  sec={s['sec_id'][:8]}... W={s['sec_w']} H={s['sec_h']} D={s['sec_d']} "
          f"pallet W={s['p_w']} H={s['p_h']} {new_flag}")

print()

# Check consolidation pairs
for w, items in sorted(by_width.items()):
    max_pair_sum = w - 3 * GAP
    widths = sorted([s["p_w"] for s in items], reverse=True)
    print(f"Section W={w}: max_pair_sum={max_pair_sum}")
    print(f"  Pallet widths: {widths[:20]}")

    # Count valid pairs (matching H/D)
    pairs_same_hd = 0
    pairs_any_hd = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sum_w = items[i]["p_w"] + items[j]["p_w"]
            if sum_w <= max_pair_sum:
                pairs_any_hd += 1
                if items[i]["sec_h"] == items[j]["sec_h"] and items[i]["sec_d"] == items[j]["sec_d"]:
                    pairs_same_hd += 1
    print(f"  Valid pairs (same H/D): {pairs_same_hd}")
    print(f"  Valid pairs (any H/D): {pairs_any_hd}")

    # Show some example pairs that DON'T fit
    if pairs_any_hd == 0 and len(widths) >= 2:
        print(f"  Why no pairs? Min two widths: {widths[-2:]} sum={widths[-2] + widths[-1]}")
    print()

# Also check: what heights/depths exist among singles?
print("Height distribution in singles:")
heights = defaultdict(int)
for s in singles:
    heights[s["sec_h"]] += 1
for h, c in sorted(heights.items()):
    print(f"  H={h}: {c}")

print()
print("Depth distribution in singles:")
depths = defaultdict(int)
for s in singles:
    depths[s["sec_d"]] += 1
for d, c in sorted(depths.items()):
    print(f"  D={d}: {c}")
