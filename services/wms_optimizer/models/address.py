"""Модель адреса в секции."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Address:
    id: str
    section_id: str
    position: int  # 1 — левый, 2 — центральный, 3 — правый
    pallet_id: Optional[str] = None
    # ЗаблокированОстаток: адрес зарезервирован, свободным местом не считается
    # и не участвует в реслоте — даже если pallet_id известен.
    blocked: bool = False

    @property
    def is_occupied(self) -> bool:
        return self.pallet_id is not None
