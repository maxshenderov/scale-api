import uuid
from pydantic import BaseModel, Field
from typing import Optional


class ConnectionIn(BaseModel):
    name: str
    url: str
    login: str = ""
    password: str = ""


class ConnectionOut(BaseModel):
    id: int
    name: str
    url: str
    login: str
    password: str
    is_active: bool


class SnapshotIn(BaseModel):
    name: str
    warehouse_name: str = ""
    data: dict


class SnapshotOut(BaseModel):
    id: int
    name: str
    warehouse_name: str
    is_active: bool
    created_at: str


class SnapshotDetail(SnapshotOut):
    data: dict


class PalletItem(BaseModel):
    id: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    width: float
    height: float
    depth: float = 1100
    weight: float = 0


class PlacementResult(BaseModel):
    pallet: str
    section: Optional[str] = None
    address: Optional[str] = None
    rack: Optional[str] = None
    floor: Optional[int] = None
    fillLevel: int = 0


class UnplacedResult(BaseModel):
    pallet: str
    reason: str


class OptimizeRequest(BaseModel):
    warehouse: str
    pallets: list[PalletItem]
    reslot: Optional[dict] = None


class OptimizeResponse(BaseModel):
    newPlacements: list[PlacementResult] = []
    reslotMoves: list[dict] = []
    unplaced: list[UnplacedResult] = []
    stats: dict = {}


class ExecuteRequest(BaseModel):
    warehouse: str
    placements: list[dict]


class FloorsRequest(BaseModel):
    warehouse: str
    rackId: str
