"""Тесты логики узкопроходных стеллажей.

Запуск: pytest tests/test_narrow_aisle.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import OptimizationSettingsSchema
from tests.test_acceptance import make_section_row, make_new_pallet, make_request
from optimizer.global_optimizer import run_optimization


def test_narrow_pallet_strict_mode_only_narrow_sections():
    """strictNarrowAislePlacement=true: узкопроходная паллета размещается только в узкопроходные секции."""
    occupancy = [
        make_section_row("SEC001", narrow_aisle=True),  # узкопроходная
        make_section_row("SEC002", narrow_aisle=False),  # широкопроходная
    ]
    # Паллета 1200x1000 — узкопроходная (width<=1200 AND depth<=1200)
    narrow_pallet = make_new_pallet("NP001", width=1200, depth=1000)
    settings = OptimizationSettingsSchema(strictNarrowAislePlacement=True)

    req = make_request(occupancy, new_pallets=[narrow_pallet], settings=settings)
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 1
    put_ops = [op for op in resp.operations if op.operation == "PUT"]
    assert len(put_ops) == 1
    # Проверяем что паллета попала именно в узкопроходную секцию SEC001
    assert put_ops[0].newAddress.startswith("SEC001-")


def test_narrow_pallet_strict_mode_no_narrow_sections_available():
    """strictNarrowAislePlacement=true: если узкопроходные секции заняты → notPlaced NARROW_AISLE_MISMATCH."""
    occupancy = [
        make_section_row("SEC001", narrow_aisle=True, width=1000, gap_width=50,
                          pallets=[{"id": "EP001", "width": 900, "height": 1500, "depth": 1000, "weight": 700}]),
        make_section_row("SEC002", narrow_aisle=False),  # свободная широкопроходная
    ]
    narrow_pallet = make_new_pallet("NP001", width=1200, depth=1000)
    settings = OptimizationSettingsSchema(strictNarrowAislePlacement=True)

    req = make_request(occupancy, new_pallets=[narrow_pallet], settings=settings)
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 0
    assert len(resp.notPlaced) == 1
    assert resp.notPlaced[0].reason == "NARROW_AISLE_MISMATCH"


def test_narrow_pallet_non_strict_mode_fallback_to_wide():
    """strictNarrowAislePlacement=false: узкопроходная паллета может быть размещена в широкопроходную секцию."""
    occupancy = [
        make_section_row("SEC001", narrow_aisle=True, width=1000, gap_width=50,
                          pallets=[{"id": "EP001", "width": 900, "height": 1500, "depth": 1000, "weight": 700}]),
        make_section_row("SEC002", narrow_aisle=False),  # свободная широкопроходная
    ]
    narrow_pallet = make_new_pallet("NP001", width=1200, depth=1000)
    settings = OptimizationSettingsSchema(strictNarrowAislePlacement=False)

    req = make_request(occupancy, new_pallets=[narrow_pallet], settings=settings)
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 1
    put_ops = [op for op in resp.operations if op.operation == "PUT"]
    assert len(put_ops) == 1
    # Должна попасть в широкопроходную SEC002 (SEC001 занята)
    assert put_ops[0].newAddress.startswith("SEC002-")


def test_narrow_sections_priority_both_free():
    """strictNarrowAislePlacement=false: узкопроходные секции имеют приоритет даже при мягком режиме."""
    occupancy = [
        make_section_row("SEC001", narrow_aisle=True),
        make_section_row("SEC002", narrow_aisle=False),
    ]
    narrow_pallet = make_new_pallet("NP001", width=1200, depth=1000)
    settings = OptimizationSettingsSchema(strictNarrowAislePlacement=False)

    req = make_request(occupancy, new_pallets=[narrow_pallet], settings=settings)
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 1
    put_ops = [op for op in resp.operations if op.operation == "PUT"]
    # Должна попасть в узкопроходную SEC001 (приоритет)
    assert put_ops[0].newAddress.startswith("SEC001-")


def test_wide_pallet_can_go_anywhere():
    """Широкопроходная паллета (width>1200 OR depth>1200) размещается в любые секции."""
    occupancy = [
        make_section_row("SEC001", narrow_aisle=True, width=3000),
        make_section_row("SEC002", narrow_aisle=False, width=3000),
    ]
    # Паллета 1300x1000 — НЕ узкопроходная (width>1200)
    wide_pallet = make_new_pallet("WP001", width=1300, depth=1000)
    settings = OptimizationSettingsSchema(strictNarrowAislePlacement=True)

    req = make_request(occupancy, new_pallets=[wide_pallet], settings=settings)
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 1
    # Может попасть в любую секцию — не проверяем конкретную, главное что размещена
