"""Section Optimizer — выбор адреса внутри секции (§9.2 ТЗ).

После того как Global Optimizer определил Паллета → Секция,
Section Optimizer решает Паллета → Адрес.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from models.address import Address
from models.pallet import Pallet
from models.section import Section


def assign_addresses(
    pallets: List[Pallet],
    section_assignment: Dict[str, Optional[str]],
    section_map: Dict[str, Section],
    address_map: Dict[str, Address],
) -> Dict[str, Optional[str]]:
    """Для каждой паллеты находит адрес в назначенной секции по правилу из 1С.

    Правило выбора адреса:
    1. Если ШиринаПаллета > Ширина_Секции * 2/3 → Адрес2 (центр)
    2. Если Паллет1 пуст → Адрес1
    3. Если Паллет3 пуст → Адрес3
    4. Если Паллет2 пуст → Адрес2
    5. Иначе → НЕ размещаем (None)

    Returns:
        {pallet_id: address_id | None}
    """
    # Секция → список адресов (отсортированных по position)
    section_addresses: Dict[str, List[Address]] = {}
    for addr in address_map.values():
        section_addresses.setdefault(addr.section_id, []).append(addr)
    for sec_id in section_addresses:
        section_addresses[sec_id].sort(key=lambda a: a.position)

    # Виртуальное состояние: кто занял какой адрес в этом расчёте
    virtual_address_pallet: Dict[str, Optional[str]] = {
        aid: addr.pallet_id for aid, addr in address_map.items()
    }

    result: Dict[str, Optional[str]] = {}

    for pallet in pallets:
        sec_id = section_assignment.get(pallet.id)
        if not sec_id:
            result[pallet.id] = None
            continue

        sec = section_map.get(sec_id)
        if not sec:
            result[pallet.id] = None
            continue

        addresses_in_sec = section_addresses.get(sec_id, [])

        # Правило 1: большая паллета → Адрес2 (центр, position=2)
        if pallet.width > sec.width * 2 / 3:
            center = next(
                (a for a in addresses_in_sec if a.position == 2
                 and virtual_address_pallet.get(a.id) is None
                 and not a.blocked),
                None
            )
            if center:
                result[pallet.id] = center.id
                virtual_address_pallet[center.id] = pallet.id
            else:
                result[pallet.id] = None
            continue

        # Правило 2: Паллет1 пуст → Адрес1
        addr1 = next((a for a in addresses_in_sec if a.position == 1), None)
        if addr1 and virtual_address_pallet.get(addr1.id) is None and not addr1.blocked:
            result[pallet.id] = addr1.id
            virtual_address_pallet[addr1.id] = pallet.id
            continue

        # Правило 3: Паллет3 пуст → Адрес3
        addr3 = next((a for a in addresses_in_sec if a.position == 3), None)
        if addr3 and virtual_address_pallet.get(addr3.id) is None and not addr3.blocked:
            result[pallet.id] = addr3.id
            virtual_address_pallet[addr3.id] = pallet.id
            continue

        # Правило 4: Паллет2 пуст → Адрес2
        addr2 = next((a for a in addresses_in_sec if a.position == 2), None)
        if addr2 and virtual_address_pallet.get(addr2.id) is None and not addr2.blocked:
            result[pallet.id] = addr2.id
            virtual_address_pallet[addr2.id] = pallet.id
            continue

        # Правило 5: Иначе не размещаем
        result[pallet.id] = None

    return result
