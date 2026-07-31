"""Unit-тесты для optimizer/potential.py (§8, §16.2 ТЗ).

Запуск: pytest tests/test_potential.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models.pallet import Pallet, PalletTypeSize
from models.section import Section, SectionTypeSize
from optimizer.potential import (
    compute_potential, compute_potential_after_placement,
    compute_potential_loss, section_fits_pallet,
)


def make_section(width=3000, height=1800, depth=1200, max_weight=3000,
                  gap_width=50, max_lift_weight=1000, narrow_aisle=True) -> Section:
    return Section(
        id="SEC001", rack_id="R01", floor=1,
        narrow_aisle=narrow_aisle,
        type_size=SectionTypeSize(
            width=width, height=height, depth=depth,
            max_weight=max_weight, gap_width=gap_width, max_lift_weight=max_lift_weight,
        ),
    )


def make_pallet(id="P001", width=900, height=1500, depth=1000, weight=700) -> Pallet:
    return Pallet(id=id, type_size=PalletTypeSize(width=width, height=height, depth=depth, weight=weight))


# ---------------------------------------------------------------------------
# section_fits_pallet (§7)
# ---------------------------------------------------------------------------

def test_fits_empty_section():
    sec = make_section()
    assert section_fits_pallet(sec, [], make_pallet()) is True


def test_fits_fails_by_count():
    sec = make_section()
    pallets = [make_pallet(f"E{i}", width=100) for i in range(3)]
    assert section_fits_pallet(sec, pallets, make_pallet()) is False


def test_fits_fails_by_width():
    sec = make_section(width=1000, gap_width=50)
    assert section_fits_pallet(sec, [], make_pallet(width=901)) is False


def test_fits_fails_by_height():
    sec = make_section(height=1000)
    assert section_fits_pallet(sec, [], make_pallet(height=1001)) is False


def test_fits_fails_by_depth():
    sec = make_section(depth=1000)
    assert section_fits_pallet(sec, [], make_pallet(depth=1001)) is False


def test_fits_fails_by_lift_limit():
    sec = make_section(max_lift_weight=500)
    assert section_fits_pallet(sec, [], make_pallet(weight=501)) is False


def test_fits_fails_by_weight():
    sec = make_section(max_weight=1000)
    assert section_fits_pallet(sec, [make_pallet("E1", weight=600)], make_pallet(weight=500)) is False


def test_fits_unlimited_weight_ignores_weight_limit():
    sec = make_section(max_weight=1000)
    sec.type_size.unlimited_weight = True
    assert section_fits_pallet(sec, [make_pallet("E1", weight=600)], make_pallet(weight=500)) is True


def test_fits_fails_by_max_width_pallet():
    sec = make_section(width=3000)
    sec.max_width_pallet = 800
    assert section_fits_pallet(sec, [], make_pallet(width=801)) is False


def test_fits_fails_by_max_depth_pallet():
    sec = make_section(depth=1200)
    sec.max_depth_pallet = 900
    assert section_fits_pallet(sec, [], make_pallet(depth=901)) is False


# ---------------------------------------------------------------------------
# compute_potential / compute_potential_after_placement / compute_potential_loss (§8.2-8.3)
# ---------------------------------------------------------------------------

def test_potential_counts_pallets_that_fit():
    sec = make_section()
    remaining = [make_pallet(f"P{i}") for i in range(3)]
    assert compute_potential(sec, [], remaining) == 3


def test_potential_zero_when_section_full():
    sec = make_section()
    current = [make_pallet(f"E{i}", width=100) for i in range(3)]
    remaining = [make_pallet("P001")]
    assert compute_potential(sec, current, remaining) == 0


def test_potential_after_placement_excludes_placed_pallet():
    sec = make_section()
    remaining = [make_pallet("P001"), make_pallet("P002")]
    # После размещения P001 в секции остаётся место для P002 (900+900+150<=3000)
    after = compute_potential_after_placement(sec, [], remaining[0], remaining)
    assert after == 1


def test_potential_loss_non_negative_when_space_shrinks():
    sec = make_section()
    remaining = [make_pallet(f"P{i}") for i in range(2)]
    loss = compute_potential_loss(sec, [], remaining[0], remaining)
    assert loss >= 0


def test_potential_loss_reflects_count_limit_even_with_wide_section():
    # §7.6: лимит 3 паллеты в секции действует независимо от ширины —
    # после размещения первой паллеты count=1, потенциал падает на 1
    # (Potential считает попадание каждой remaining-паллеты независимо, не набор одновременно).
    sec = make_section(width=10000)
    remaining = [make_pallet(f"P{i}", width=100) for i in range(5)]
    loss = compute_potential_loss(sec, [], remaining[0], remaining)
    assert loss == 1
