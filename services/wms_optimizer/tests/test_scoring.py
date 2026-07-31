"""Unit-тесты для optimizer/scoring.py (§9, §16.1 ТЗ).

Запуск: pytest tests/test_scoring.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from optimizer.scoring import (
    GlobalScoreComponents, AddressScoreComponents,
    compute_global_score, compute_address_score, _WEIGHTS,
)


def test_global_score_rewards_placed_pallets():
    base = compute_global_score(GlobalScoreComponents())
    with_placed = compute_global_score(GlobalScoreComponents(placed_pallets=1))
    assert with_placed > base
    assert with_placed - base == _WEIGHTS["globalWeights"]["placedPalletWeight"]


def test_global_score_penalizes_section_moves():
    base = compute_global_score(GlobalScoreComponents(placed_pallets=1))
    moved = compute_global_score(GlobalScoreComponents(placed_pallets=1, section_moves=1))
    assert moved < base
    assert base - moved == _WEIGHTS["globalWeights"]["sectionMovePenalty"]


def test_global_score_penalizes_address_moves():
    base = compute_global_score(GlobalScoreComponents(placed_pallets=1))
    moved = compute_global_score(GlobalScoreComponents(placed_pallets=1, address_moves=1))
    assert base - moved == _WEIGHTS["globalWeights"]["addressMovePenalty"]


def test_global_score_penalizes_potential_loss():
    base = compute_global_score(GlobalScoreComponents(placed_pallets=1))
    lossy = compute_global_score(GlobalScoreComponents(placed_pallets=1, potential_loss=1))
    assert base - lossy == _WEIGHTS["globalWeights"]["potentialLossPenalty"]


def test_global_score_penalizes_used_sections():
    base = compute_global_score(GlobalScoreComponents(placed_pallets=1))
    used = compute_global_score(GlobalScoreComponents(placed_pallets=1, used_sections=1))
    assert base - used == _WEIGHTS["globalWeights"]["sectionUsagePenalty"]


def test_global_score_no_magic_weight_names():
    # §16.1: запрещены имена W1/W2/W3 — коэффициенты только содержательные
    gw = _WEIGHTS["globalWeights"]
    lw = _WEIGHTS["localWeights"]
    for key in list(gw.keys()) + list(lw.keys()):
        assert not key.upper().startswith("W") or not key[1:].isdigit(), (
            f"Коэффициент '{key}' похож на магическое имя W1/W2/W3"
        )


def test_address_score_penalizes_width_residual():
    base = compute_address_score(AddressScoreComponents())
    residual = compute_address_score(AddressScoreComponents(width_residual=10))
    assert base - residual == _WEIGHTS["localWeights"]["widthResidualPenalty"] * 10


def test_address_score_rewards_future_potential():
    base = compute_address_score(AddressScoreComponents())
    future = compute_address_score(AddressScoreComponents(future_potential=1))
    assert future - base == _WEIGHTS["localWeights"]["futurePotentialReward"]


def test_address_score_penalizes_potential_loss():
    base = compute_address_score(AddressScoreComponents())
    lossy = compute_address_score(AddressScoreComponents(potential_loss=1))
    assert base - lossy == _WEIGHTS["localWeights"]["potentialLossPenalty"]
