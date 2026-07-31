"""Единый модуль расчёта потенциала секции (§8 ТЗ).

Все уровни системы (Global Optimizer, Section Optimizer, метрики результата)
используют ТОЛЬКО функции этого модуля — без параллельных реализаций.
"""
from typing import Dict, List

from models.pallet import Pallet
from models.section import Section


def compute_potential(
    section: Section,
    pallets_in_section: List[Pallet],
    remaining_pallets: List[Pallet],
) -> int:
    """Потенциал секции — сколько паллет из remaining_pallets гипотетически поместятся.

    §8.2: для каждой необработанной паллеты p проверяем Fits(p).
    """
    free_width = _free_width(section, pallets_in_section)
    current_weight = sum(p.weight for p in pallets_in_section)
    count = len(pallets_in_section)

    result = 0
    for p in remaining_pallets:
        if _fits(p, section, free_width, current_weight, count):
            result += 1
    return result


def compute_potential_after_placement(
    section: Section,
    pallets_in_section: List[Pallet],
    new_pallet: Pallet,
    remaining_pallets: List[Pallet],
) -> int:
    """Потенциал секции после гипотетического размещения new_pallet (§8.3, §9.2).

    Вызывает ту же функцию compute_potential с обновлённым состоянием.
    """
    updated = pallets_in_section + [new_pallet]
    # Из remaining убираем new_pallet (она уже размещена)
    rest = [p for p in remaining_pallets if p.id != new_pallet.id]
    return compute_potential(section, updated, rest)


def compute_potential_loss(
    section: Section,
    pallets_in_section: List[Pallet],
    new_pallet: Pallet,
    remaining_pallets: List[Pallet],
) -> int:
    """PotentialLoss = PotentialBefore - PotentialAfter (§8.3)."""
    before = compute_potential(section, pallets_in_section, remaining_pallets)
    after = compute_potential_after_placement(section, pallets_in_section, new_pallet, remaining_pallets)
    return before - after


# ---------------------------------------------------------------------------
# Внутренние помощники
# ---------------------------------------------------------------------------

def _free_width(section: Section, pallets: List[Pallet]) -> float:
    """FreeWidth = SectionWidth - SUM(PalletWidth) - (N+1)*GapWidth."""
    n = len(pallets)
    used_width = sum(p.width for p in pallets)
    return section.width - used_width - (n + 1) * section.gap_width


def _fits(pallet: Pallet, section: Section, free_width: float, current_weight: float, current_count: int) -> bool:
    """Проверяет Fits(p) по §8.2."""
    # Ширина: нужно дополнительно занять ширину паллеты + ещё один GapWidth
    if free_width < pallet.width + section.gap_width:
        return False
    # Высота
    if pallet.height > section.height:
        return False
    # Глубина
    if pallet.depth > section.depth:
        return False
    # Вес секции (section.max_weight = inf при unlimited_weight)
    if current_weight + pallet.weight > section.max_weight:
        return False
    # Количество мест
    if current_count >= section.max_pallets:
        return False
    # Максимальный размер одной паллеты (узкопроходные стеллажи, §7 ТЗ)
    if pallet.width > section.eff_max_width:
        return False
    if pallet.depth > section.eff_max_depth:
        return False
    return True


def section_fits_pallet(section: Section, pallets_in_section: List[Pallet], pallet: Pallet, strict_narrow: bool = True) -> bool:
    """Проверяет все ограничения §7 для размещения паллеты в секции.

    strict_narrow: если True, узкопроходная паллета размещается только в узкопроходные секции.
                   если False, узкопроходные секции имеют приоритет, но паллета может быть размещена
                   в широкопроходную секцию, если узкопроходные заняты.
    """
    # Узкопроходная паллета → только узкопроходные секции (если strict_narrow=True)
    if strict_narrow and pallet.is_narrow and not section.narrow_aisle:
        return False
    count = len(pallets_in_section)
    # §7.6 количество
    if count >= section.max_pallets:
        return False
    # §7.3 высота
    if pallet.height > section.height:
        return False
    # §7.4 глубина
    if pallet.depth > section.depth:
        return False
    # §7.5 подъём
    if pallet.weight > section.max_lift_weight:
        return False
    # Максимальный размер одной паллеты (узкопроходные стеллажи)
    if pallet.width > section.eff_max_width:
        return False
    if pallet.depth > section.eff_max_depth:
        return False
    # §7.1 ширина
    total_width = sum(p.width for p in pallets_in_section) + pallet.width
    total_gap = (count + 2) * section.gap_width  # N+1 зазоров после добавления
    if total_width + total_gap > section.width:
        return False
    # §7.2 вес (section.max_weight = inf при unlimited_weight)
    total_weight = sum(p.weight for p in pallets_in_section) + pallet.weight
    if total_weight > section.max_weight:
        return False
    return True
