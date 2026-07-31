"""Модель паллеты."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PalletTypeSize:
    width: float
    height: float
    depth: float
    weight: float


@dataclass
class Pallet:
    id: str
    type_size: PalletTypeSize
    code: str = ""
    # Для существующих паллет — текущее размещение
    current_address_id: Optional[str] = None
    current_section_id: Optional[str] = None
    # ЗаблокированОстаток: паллета физически стоит (или зарезервирована), но
    # переставлять её нельзя — адрес занят, но не участвует в реслоте.
    movable: bool = True
    # Для новых паллет — приоритет доступа (резерв на будущее: сортировка секций
    # по accessLevel/accessTime в зависимости от значения 1 или 2).
    access_level: int = 1

    @property
    def width(self) -> float:
        return self.type_size.width

    @property
    def height(self) -> float:
        return self.type_size.height

    @property
    def depth(self) -> float:
        return self.type_size.depth

    @property
    def weight(self) -> float:
        return self.type_size.weight

    @property
    def is_narrow(self) -> bool:
        """Узкопроходная паллета: ширина И глубина ≤ 1200мм."""
        return self.width <= 1200 and self.depth <= 1200
