"""Pydantic-схемы для REST API — контракт с Лико_WMS_Сервер.WMS_GetOccupancy (1С)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Section Packing Schemas (для /api/pack_section)
# ---------------------------------------------------------------------------

class PalletTypeSchema(BaseModel):
    """Типоразмер паллеты с остатком."""
    width: float = Field(..., gt=0, description="Ширина паллеты (мм)")
    height: float = Field(..., gt=0, description="Высота паллеты (мм)")
    depth: float = Field(..., gt=0, description="Глубина паллеты (мм)")
    weight: float = Field(..., ge=0, description="Вес паллеты (кг)")
    count: int = Field(..., ge=0, description="Доступное количество этого типа")


class SectionConstraintsSchema(BaseModel):
    """Физические ограничения секции."""
    width: float = Field(..., gt=0, description="Ширина секции (мм)")
    height: float = Field(..., gt=0, description="Высота секции (мм)")
    depth: float = Field(..., gt=0, description="Глубина секции (мм)")
    max_pallets: int = Field(3, ge=1, description="Максимум паллет в секции")
    max_weight: Optional[float] = Field(None, description="Макс. вес (кг), null = без ограничения")
    narrow_aisle: bool = Field(False, description="Узкопроходный стеллаж")
    max_width_pallet: Optional[float] = Field(None, description="Макс. ширина паллеты для узкопроходного")


class PackSectionRequest(BaseModel):
    """Запрос на оптимальное заполнение секции."""
    section: SectionConstraintsSchema
    availableTypes: List[PalletTypeSchema] = Field(..., min_length=1)


class SelectedTypeSchema(BaseModel):
    """Выбранный типоразмер с количеством."""
    typeIndex: int = Field(..., ge=0, description="Индекс в массиве availableTypes")
    count: int = Field(..., ge=1, description="Количество паллет этого типа")


class PackSectionResponse(BaseModel):
    """Ответ с оптимальным решением."""
    selected: List[SelectedTypeSchema] = Field(..., description="Выбранные типы для размещения")
    usedWidth: float = Field(..., description="Использованная ширина (мм, с зазорами)")
    usedPallets: int = Field(..., description="Размещено паллет")
    usedWeight: float = Field(..., description="Суммарный вес (кг)")
    utilization: float = Field(..., ge=0, le=1, description="Коэффициент использования ширины")


# ---------------------------------------------------------------------------
# Occupancy — построчная копия того, что возвращает WMS_GetOccupancy (1С):
# одна строка = одна секция с её текущей занятостью (до 3 адресов/паллет).
# См. Лико_WMS_Сервер.СобратьЗанятостьСекций().
# ---------------------------------------------------------------------------

class OccupancySectionSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section_id: str
    section_code: str
    rack_id: str
    rack_code: int
    floor: int
    accessLevel: int = 1
    accessTime: float = 0
    restricted: bool = False
    narrowAisle: bool = False

    typeSize_width: float = Field(..., gt=0)
    typeSize_height: float = Field(..., gt=0)
    typeSize_depth: float = Field(..., gt=0)
    typeSize_weight: float = Field(..., ge=0)
    typeSize_unlimitedWeight: bool = False

    gap_width: float = Field(..., ge=0)
    max_lift_weight: float = Field(..., ge=0)
    max_pallets: int = 3
    # max_widthPallet: уже с fallback на ширину секции (резолвится в SQL 1С).
    # max_depthPallet: БЕЗ fallback — 0 означает "нет отдельного ограничения".
    max_widthPallet: float = 0
    max_depthPallet: float = 0

    address1: str = ""
    address2: str = ""
    address3: str = ""

    pallet1_id: str = ""
    pallet1_code: str = ""
    pallet1_width: float = 0
    pallet1_height: float = 0
    pallet1_depth: float = 0
    pallet1_weight: float = 0
    quantity1: float = 0
    blocked1: float = 0

    pallet2_id: str = ""
    pallet2_code: str = ""
    pallet2_width: float = 0
    pallet2_height: float = 0
    pallet2_depth: float = 0
    pallet2_weight: float = 0
    quantity2: float = 0
    blocked2: float = 0

    pallet3_id: str = ""
    pallet3_code: str = ""
    pallet3_width: float = 0
    pallet3_height: float = 0
    pallet3_depth: float = 0
    pallet3_weight: float = 0
    quantity3: float = 0
    blocked3: float = 0

    @field_validator("pallet1_code", "pallet2_code", "pallet3_code", mode="before")
    @classmethod
    def convert_code_to_str(cls, v: Any) -> str:
        """1С передает pallet_code как int, но схема требует str — автоконверсия."""
        if v is None or v == "":
            return ""
        return str(v)


class NewPalletSchema(BaseModel):
    id: str
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)
    depth: float = Field(..., gt=0)
    weight: float = Field(..., ge=0)
    accessLevel: int = 1


class OptimizationSettingsSchema(BaseModel):
    allowReslot: bool = True
    maxReslotPercent: float = Field(20.0, ge=0, le=100)
    maxOperations: int = Field(300, ge=0)
    timeLimitSeconds: int = Field(120, ge=1)
    strictNarrowAislePlacement: bool = True
    twoStageReslot: bool = Field(
        False,
        description="Двухэтапный режим: ЭТАП 1 без реслота, ЭТАП 2 с реслотом для не размещённых. "
                    "Работает только при mode='place'. Игнорирует allowReslot на ЭТАПЕ 1."
    )
    twoStageReslotMaxReslotPercent: float = Field(
        10.0, ge=0, le=100,
        description="maxReslotPercent для ЭТАПА 2 (если twoStageReslot=True)"
    )
    twoStageReslotTimeLimitSeconds: int = Field(
        120, ge=1,
        description="timeLimitSeconds для ЭТАПА 2 (если twoStageReslot=True)"
    )
    solverType: Literal["cp_sat", "numpy", "lp", "hybrid_v3", "hybrid-v3", "hybrid_v5", "hybrid-v5"] = Field(
        "cp_sat",
        description="Тип солвера: 'cp_sat' (OR-Tools, макс. качество), "
        "'numpy' (NumPy greedy), "
        "'hybrid-v3' (BFD+Chain-Swap, быстрый), "
        "'hybrid-v5' (Aggregate CP-SAT + V3 reslot, качество)"
    )


# ---------------------------------------------------------------------------
# Запрос
# ---------------------------------------------------------------------------

class OptimizationRequest(BaseModel):
    optimizationId: str = ""
    mode: Literal["place", "compact"] = "place"
    occupancy: List[OccupancySectionSchema] = Field(default_factory=list)
    newPallets: List[NewPalletSchema] = Field(default_factory=list)
    settings: OptimizationSettingsSchema = Field(default_factory=OptimizationSettingsSchema)

    @model_validator(mode="after")
    def _check_mode_new_pallets(self):
        if self.mode == "compact" and self.newPallets:
            raise ValueError("mode='compact' не принимает newPallets — только реслот существующих паллет")
        return self


# ---------------------------------------------------------------------------
# Ответ
# ---------------------------------------------------------------------------

class SolverStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    TIME_LIMIT = "TIME_LIMIT"
    INFEASIBLE = "INFEASIBLE"


class PlacementStatus(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OperationSchema(BaseModel):
    pallet: str
    operation: Literal["PUT", "MOVE"]
    oldAddress: Optional[str] = None
    newAddress: str
    sequence: int


class NotPlacedSchema(BaseModel):
    pallet: str
    reason: str
    details: Dict[str, Any] = Field(default_factory=dict)


class MetricsSchema(BaseModel):
    placedPallets: int
    notPlacedPallets: int
    movedPallets: int
    potentialLoss: int
    usedSections: int


class OptimizationResponse(BaseModel):
    optimizationId: str
    mode: str
    solverStatus: SolverStatus
    placementStatus: PlacementStatus
    score: float
    executionTimeSeconds: float
    operations: List[OperationSchema] = Field(default_factory=list)
    notPlaced: List[NotPlacedSchema] = Field(default_factory=list)
    metrics: MetricsSchema


class AsyncJobResponse(BaseModel):
    optimizationId: str
    status: JobStatus
    progress: int = 0


class AsyncJobResultResponse(OptimizationResponse):
    pass
