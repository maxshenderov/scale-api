"""First Fit Decreasing — эвристика для Warm Start в CP-SAT (§11 шаг 3-4 ТЗ).

Строит начальное решение: сортирует паллеты по убыванию ширины,
жадно назначает каждую в первую подходящую секцию.
Результат передаётся в CP-SAT как starting hints.
"""
from typing import Dict, List

from models.address import Address
from models.pallet import Pallet
from models.section import Section
from optimizer.potential import section_fits_pallet


def first_fit_decreasing(
    new_pallets: List[Pallet],
    existing_pallets: List[Pallet],
    sections: List[Section],
    addresses: List[Address],
    allow_reslot: bool,
    strict_narrow: bool = True,
) -> Dict[str, str]:
    """Возвращает {pallet_id: section_id} — начальное назначение.

    Заблокированные (movable=False) паллеты никогда не двигаются — независимо
    от allow_reslot, они всегда фиксированы на текущем месте. Обычные существующие
    паллеты двигаются только если allow_reslot=True.
    """
    # Состояние: section_id -> список паллет в этой секции
    state: Dict[str, List[Pallet]] = {s.id: [] for s in sections}

    pallet_to_section: Dict[str, str] = {}
    for addr in addresses:
        if addr.pallet_id is not None:
            pallet_to_section[addr.pallet_id] = addr.section_id

    def is_fixed(p: Pallet) -> bool:
        return not p.movable or not allow_reslot

    # Фиксированные существующие паллеты — сразу в state и в assignment
    assignment: Dict[str, str] = {}
    for p in existing_pallets:
        if is_fixed(p):
            sec_id = pallet_to_section.get(p.id)
            if sec_id in state:
                state[sec_id].append(p)
                assignment[p.id] = sec_id

    # Паллеты для размещения: новые + движимые существующие (при allow_reslot)
    to_place: List[Pallet] = list(new_pallets)
    to_place += [p for p in existing_pallets if not is_fixed(p)]

    to_place_sorted = sorted(to_place, key=lambda p: p.width, reverse=True)
    # Приоритет узкопроходных секций: сначала narrow_aisle=True, потом по ширине
    sections_sorted = sorted(sections, key=lambda s: (not s.narrow_aisle, -s.width))

    for pallet in to_place_sorted:
        for sec in sections_sorted:
            current = state[sec.id]
            if section_fits_pallet(sec, current, pallet, strict_narrow):
                state[sec.id].append(pallet)
                assignment[pallet.id] = sec.id
                break
        # Если не нашли секцию — оставляем без назначения (CP-SAT попробует сам)

    return assignment
