"""Handoff validation: run optimizer on S7 data and check ALL 17 1С rules.

Usage:
  python test_handoff_validation.py           # standalone
  pytest test_handoff_validation.py -v -s     # pytest

Loads warehouse7.json + floor7.json from THIS directory, runs the optimizer,
and simulates every check from 1С ОшибкиРазмещенияВАдрес() on each operation.

Imports validation logic from tests/test_validate_operations.py (single source
of truth — no code duplication).
"""
import json
import os
import sys
from collections import Counter

# Path: docs/handoff/ → 3 levels up to wms_optimizer/ service root
_HANDOFF_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVICE_DIR = os.path.dirname(os.path.dirname(_HANDOFF_DIR))
sys.path.insert(0, _SERVICE_DIR)

from api.schemas import (
    NewPalletSchema,
    OccupancySectionSchema,
    OptimizationRequest,
    OptimizationSettingsSchema,
)
from models.occupancy_builder import build_warehouse_state
from optimizer.global_optimizer import run_optimization

# Reuse existing validation — single source of truth for all 17 1С checks
from tests.test_validate_operations import (
    _validate_operations,
    _print_errors,
)

# ---------------------------------------------------------------------------
# Optimizer settings — matches test_validate_operations.py (numpy, two-stage, 300s)
# ---------------------------------------------------------------------------
SETTINGS = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=300,
    strictNarrowAislePlacement=True,
    twoStageReslot=True,
    twoStageReslotMaxReslotPercent=40.0,
    twoStageReslotTimeLimitSeconds=120,
    solverType="numpy",
)


# ---------------------------------------------------------------------------
# Data loading from handoff directory
# ---------------------------------------------------------------------------

def _load_handoff_occupancy():
    """Load warehouse7.json — 1530 empty sections, cold start."""
    path = os.path.join(_HANDOFF_DIR, "warehouse7.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"warehouse7.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [OccupancySectionSchema(**row) for row in raw["sections"]]


def _load_handoff_floor_pallets():
    """Load floor7.json — 3406 pallets from warehouse floor."""
    path = os.path.join(_HANDOFF_DIR, "floor7.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"floor7.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"],
            height=p["height"],
            depth=p["depth"],
            weight=p["weight"],
        )
        for i, p in enumerate(raw["floorPallets"])
    ]


def _load_reference_count():
    """Count pallets in manual standard (warehouse6_standard.json)."""
    path = os.path.join(_HANDOFF_DIR, "warehouse6_standard.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"warehouse6_standard.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    occupancy_reference = [OccupancySectionSchema(**row) for row in raw["sections"]]
    _, _, reference_pallets = build_warehouse_state(occupancy_reference)
    return len(reference_pallets)


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------

def run_handoff_validation():
    """Run optimizer and validate all operations against 1С rules.

    Returns:
        (passed, error_count, duplicate_count, total_ops, resp) tuple
    """
    print("=" * 70)
    print("HANDOFF VALIDATION: WMS Pallet Optimizer")
    print("=" * 70)

    # --- Load data ---
    print("\n[1/5] Loading data...")
    occupancy_s7 = _load_handoff_occupancy()
    floor_pallets = _load_handoff_floor_pallets()
    reference_count = _load_reference_count()
    print(f"  Occupancy: {len(occupancy_s7)} sections")
    print(f"  Floor pallets: {len(floor_pallets)}")
    print(f"  Manual reference (S6): {reference_count}/{len(floor_pallets)} "
          f"({reference_count / len(floor_pallets) * 100:.1f}%)")

    # --- Run optimizer ---
    print(f"\n[2/5] Running optimizer (two-stage, numpy, 300s+120s, 40% reslot)...")
    req = OptimizationRequest(
        optimizationId="HANDOFF-VALIDATE",
        mode="place",
        occupancy=occupancy_s7,
        newPallets=floor_pallets,
        settings=SETTINGS,
    )
    resp = run_optimization(req)

    print(f"  solverStatus: {resp.solverStatus}")
    print(f"  placementStatus: {resp.placementStatus}")
    print(f"  score: {resp.score:.0f}")
    print(f"  time: {resp.executionTimeSeconds:.1f}s")
    print(f"  placed: {resp.metrics.placedPallets}/{len(floor_pallets)} "
          f"({resp.metrics.placedPallets / len(floor_pallets) * 100:.1f}%)")
    print(f"  moved: {resp.metrics.movedPallets}")
    print(f"  operations: {len(resp.operations)}")
    print(f"  notPlaced: {resp.metrics.notPlacedPallets}")

    if resp.notPlaced:
        reasons = Counter(np.reason for np in resp.notPlaced)
        print(f"  reasons: {dict(reasons)}")

    # --- Compare with manual reference (informational — numpy < CP-SAT) ---
    print(f"\n[3/5] Comparing with manual reference (S6)...")
    delta = resp.metrics.placedPallets - reference_count
    print(f"  Info: {delta:+d} vs manual ({reference_count})")
    print(f"  Note: numpy solver places fewer than CP-SAT (3332 vs 3167).")
    print(f"        Use solverType='cp_sat' for maximum placement.")

    # --- Validate all operations against 1С rules ---
    print(f"\n[4/5] Validating {len(resp.operations)} operations "
          f"against 1С ОшибкиРазмещенияВАдрес()...")
    errors, duplicates, error_reasons, virtual_state, section_by_id, \
        address_by_id, pallet_dimensions = _validate_operations(
            resp, occupancy_s7, floor_pallets
        )

    error_count = len(errors)
    duplicate_count = len(duplicates)

    # --- Report ---
    print(f"\n[5/5] RESULTS")
    print("=" * 70)
    print(f"  Operations:         {len(resp.operations)}")
    print(f"  Validation errors:  {error_count}")
    print(f"  Duplicate addresses: {duplicate_count}")
    print(f"  Error types:         {dict(error_reasons)}")
    print(f"  Placed vs manual:   {resp.metrics.placedPallets} vs "
          f"{reference_count} ({delta:+d})")

    if error_count > 0 or duplicate_count > 0:
        _print_errors(
            errors, error_reasons, section_by_id, address_by_id,
            pallet_dimensions, virtual_state,
        )
        if duplicates:
            print(f"\nDUPLICATE ADDRESSES ({duplicate_count}):")
            for addr, pallets in list(duplicates.items())[:10]:
                print(f"  {addr}: {pallets}")
        print(f"\nFAIL: {error_count} errors, {duplicate_count} duplicates")
        return False, error_count, duplicate_count, len(resp.operations), resp

    # --- All checks passed ---
    all_passed = error_count == 0 and duplicate_count == 0
    regr_passed = delta >= 0

    if all_passed:
        print(f"\nALL CHECKS PASSED")
        print(f"  [OK] 0 validation errors (all 17 1C checks)")
        print(f"  [OK] 0 duplicate addresses")
        if regr_passed:
            print(f"  [OK] Not worse than manual ({delta:+d} pallets)")
        else:
            print(f"  [info] numpy placed fewer than manual ({delta} pallets)")
    else:
        print(f"\nFAIL: {error_count} errors, {duplicate_count} duplicates")

    print("=" * 70)
    return all_passed, error_count, duplicate_count, \
        len(resp.operations), resp


# ---------------------------------------------------------------------------
# Entry points: standalone + pytest
# ---------------------------------------------------------------------------

def test_handoff_validation():
    """Pytest entry point."""
    passed, errors, dups, total, resp = run_handoff_validation()
    assert dups == 0, f"{dups} duplicate addresses in plan"
    assert errors == 0, (
        f"{errors}/{total} operations failed 1С validation"
    )


if __name__ == "__main__":
    passed, errors, dups, total, resp = run_handoff_validation()
    if not passed:
        sys.exit(1)
