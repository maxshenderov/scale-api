"""Модель секции склада."""
import math
from dataclasses import dataclass


@dataclass
class SectionTypeSize:
    width: float
    height: float
    depth: float
    max_weight: float
    gap_width: float
    max_lift_weight: float
    unlimited_weight: bool = False


@dataclass
class Section:
    id: str
    type_size: SectionTypeSize
    code: str = ""
    rack_id: str = ""
    rack_code: int = 0
    floor: int = 1
    restricted: bool = False
    narrow_aisle: bool = False
    max_pallets: int = 3
    # Максимальный размер ОДНОЙ паллеты (узкопроходные стеллажи, §7 ТЗ).
    # 0 = нет отдельного ограничения — используется полный размер секции.
    max_width_pallet: float = 0.0
    max_depth_pallet: float = 0.0
    access_level: int = 1
    access_time: float = 0.0

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
    def max_weight(self) -> float:
        return math.inf if self.type_size.unlimited_weight else self.type_size.max_weight

    @property
    def gap_width(self) -> float:
        return self.type_size.gap_width

    @property
    def max_lift_weight(self) -> float:
        return self.type_size.max_lift_weight

    @property
    def eff_max_width(self) -> float:
        # 1С уже резолвит 0 → ширина секции (CASE в SQL), но проверяем сами защитно.
        return self.max_width_pallet if self.max_width_pallet > 0 else self.width

    @property
    def eff_max_depth(self) -> float:
        # 1С НЕ резолвит этот fallback (в отличие от ширины) — применяем его сами.
        return self.max_depth_pallet if self.max_depth_pallet > 0 else self.depth
