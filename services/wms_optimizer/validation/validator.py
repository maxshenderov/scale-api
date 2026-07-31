"""Валидация входных данных запроса оптимизации."""
from typing import List, Optional

from api.schemas import OptimizationRequest

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


class ValidationError(Exception):
    def __init__(self, reason: str, details: Optional[str] = None):
        self.reason = reason
        self.details = details
        super().__init__(f"{reason}: {details}")


def _is_empty(value: str) -> bool:
    return not value or value == EMPTY_GUID


def validate_request(req: OptimizationRequest) -> None:
    """Валидация ссылочной целостности occupancy + newPallets.

    Структурные проверки (обязательные поля, диапазоны размеров) уже выполнены
    Pydantic при разборе OptimizationRequest. Здесь проверяется целостность,
    которую Pydantic не видит: дубликаты id внутри плоской таблицы occupancy.

    При нарушении бросает ValidationError с reason=INVALID_DATA.
    """
    _check_duplicates([row.section_id for row in req.occupancy], "occupancy.section_id")

    address_ids: List[str] = []
    pallet_ids: List[str] = []
    for row in req.occupancy:
        for addr_id, pallet_id in (
            (row.address1, row.pallet1_id),
            (row.address2, row.pallet2_id),
            (row.address3, row.pallet3_id),
        ):
            if not _is_empty(addr_id):
                address_ids.append(addr_id)
            if not _is_empty(pallet_id):
                pallet_ids.append(pallet_id)

    _check_duplicates(address_ids, "occupancy.address")
    _check_duplicates(pallet_ids, "occupancy.pallet")

    new_pallet_ids = [p.id for p in req.newPallets]
    _check_duplicates(new_pallet_ids, "newPallets")

    existing_pallet_id_set = set(pallet_ids)
    for pid in new_pallet_ids:
        if pid in existing_pallet_id_set:
            raise ValidationError(
                "INVALID_DATA",
                f"newPallets содержит id '{pid}', уже занятый существующей паллетой в occupancy",
            )


def _check_duplicates(ids: List[str], entity: str) -> None:
    seen = set()
    for id_ in ids:
        if id_ in seen:
            raise ValidationError(
                "INVALID_DATA",
                f"дублирующийся id '{id_}' в {entity}",
            )
        seen.add(id_)
