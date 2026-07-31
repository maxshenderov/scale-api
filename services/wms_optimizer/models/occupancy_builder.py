"""Построение внутренних моделей Section/Address/Pallet из плоской occupancy-таблицы 1С.

Формат occupancy — результат Лико_WMS_Сервер.WMS_GetOccupancy: одна строка на секцию,
до 3 адресов (address1..3) с паллетами (pallet1..3) внутри. См. СобратьЗанятостьСекций()
в 1s/ERP/extensions/liko/CommonModules/Лико_WMS_Сервер/Ext/Module.bsl.
"""
from typing import List, Tuple

from api.schemas import OccupancySectionSchema
from models.address import Address
from models.pallet import Pallet, PalletTypeSize
from models.section import Section, SectionTypeSize

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def _is_empty(value: str) -> bool:
    return not value or value == EMPTY_GUID


def build_warehouse_state(
    occupancy: List[OccupancySectionSchema],
) -> Tuple[List[Section], List[Address], List[Pallet]]:
    """Возвращает (sections, addresses, existing_pallets).

    Секции с restricted=True полностью исключаются — не участвуют в оптимизации
    ни для новых, ни для существующих паллет.
    """
    sections: List[Section] = []
    addresses: List[Address] = []
    existing_pallets: List[Pallet] = []

    for row in occupancy:
        if row.restricted:
            continue

        sections.append(Section(
            id=row.section_id,
            code=row.section_code,
            rack_id=row.rack_id,
            rack_code=row.rack_code,
            floor=row.floor,
            type_size=SectionTypeSize(
                width=row.typeSize_width,
                height=row.typeSize_height,
                depth=row.typeSize_depth,
                max_weight=row.typeSize_weight,
                gap_width=row.gap_width,
                max_lift_weight=row.max_lift_weight,
                unlimited_weight=row.typeSize_unlimitedWeight,
            ),
            restricted=row.restricted,
            narrow_aisle=row.narrowAisle,
            max_pallets=row.max_pallets if row.max_pallets > 0 else 3,
            max_width_pallet=row.max_widthPallet,
            max_depth_pallet=row.max_depthPallet,
            access_level=row.accessLevel,
            access_time=row.accessTime,
        ))

        for position, addr_id, pallet_id, pallet_code, width, height, depth, weight, quantity, blocked in (
            (1, row.address1, row.pallet1_id, row.pallet1_code, row.pallet1_width, row.pallet1_height, row.pallet1_depth, row.pallet1_weight, row.quantity1, row.blocked1),
            (2, row.address2, row.pallet2_id, row.pallet2_code, row.pallet2_width, row.pallet2_height, row.pallet2_depth, row.pallet2_weight, row.quantity2, row.blocked2),
            (3, row.address3, row.pallet3_id, row.pallet3_code, row.pallet3_width, row.pallet3_height, row.pallet3_depth, row.pallet3_weight, row.quantity3, row.blocked3),
        ):
            if _is_empty(addr_id):
                continue

            is_blocked = blocked > 0
            has_pallet = not _is_empty(pallet_id) and (quantity > 0 or is_blocked)

            addresses.append(Address(
                id=addr_id,
                section_id=row.section_id,
                position=position,
                pallet_id=pallet_id if has_pallet else None,
                blocked=is_blocked,
            ))

            if has_pallet:
                existing_pallets.append(Pallet(
                    id=pallet_id,
                    code=pallet_code,
                    type_size=PalletTypeSize(width=width, height=height, depth=depth, weight=weight),
                    current_address_id=addr_id,
                    current_section_id=row.section_id,
                    movable=not is_blocked,
                ))

    return sections, addresses, existing_pallets
