"""Расчёт целевых функций: GlobalScore и AddressScore (§9 ТЗ).

Все коэффициенты берутся из config/weights.json — захардкоживание запрещено.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict


def _load_weights() -> Dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "weights.json")
    with open(os.path.normpath(config_path), "r", encoding="utf-8") as f:
        return json.load(f)


_WEIGHTS = _load_weights()


@dataclass
class GlobalScoreComponents:
    placed_pallets: int = 0
    section_moves: int = 0
    address_moves: int = 0
    potential_loss: int = 0
    unused_space: float = 0.0
    used_sections: int = 0


@dataclass
class AddressScoreComponents:
    width_residual: float = 0.0
    future_potential: int = 0
    potential_loss: int = 0


def compute_global_score(components: GlobalScoreComponents) -> float:
    """GlobalScore = §9.1 целевая функция. Максимизируется."""
    gw = _WEIGHTS["globalWeights"]
    return (
        gw["placedPalletWeight"] * components.placed_pallets
        - gw["sectionMovePenalty"] * components.section_moves
        - gw["addressMovePenalty"] * components.address_moves
        - gw["potentialLossPenalty"] * components.potential_loss
        - gw["spaceLossPenalty"] * components.unused_space
        - gw["sectionUsagePenalty"] * components.used_sections
    )


def compute_address_score(components: AddressScoreComponents) -> float:
    """AddressScore = §9.2 локальная оценка адреса. Максимизируется."""
    lw = _WEIGHTS["localWeights"]
    return (
        - lw["widthResidualPenalty"] * components.width_residual
        + lw["futurePotentialReward"] * components.future_potential
        - lw["potentialLossPenalty"] * components.potential_loss
    )


def reload_weights() -> None:
    """Перечитать weights.json без перезапуска сервиса."""
    global _WEIGHTS
    _WEIGHTS = _load_weights()
