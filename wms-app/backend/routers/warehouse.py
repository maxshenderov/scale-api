from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.connection import get_db
from services.wms_client import WmsClient
from models.schemas import OptimizeRequest, OptimizeResponse, FloorsRequest
from services.mock_data import MOCK_SNAPSHOT

router = APIRouter(prefix="/api")


async def get_active_connection(db: AsyncSession) -> dict | None:
    """Активное 1С-подключение или None (офлайн-режим). Не бросает исключения."""
    try:
        result = await db.execute(
            text("SELECT url, login, password FROM connections WHERE is_active = true LIMIT 1")
        )
        row = result.fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {"url": row[0], "login": row[1], "password": row[2]}


async def call_1c(proc: str, db: AsyncSession, **params) -> dict:
    """Вызов 1С. Бросает RuntimeError/исключение httpx, если 1С недоступен —
    роутеры данных ловят его и отдают MOCK (офлайн-режим)."""
    conn = await get_active_connection(db)
    if not conn:
        raise RuntimeError("No active 1C connection")
    client = WmsClient.from_connection(conn)
    return await client.call(proc, **params)


# --- Warehouse endpoints: 1С → MOCK fallback (офлайн-режим) ---

MOCK_WAREHOUSES = {"warehouses": [{"id": "wh-1", "name": "ЛК Высотный", "code": "000000001"}]}


@router.post("/warehouses")
async def get_warehouses(db: AsyncSession = Depends(get_db)):
    try:
        return await call_1c("WMS_GetWarehouses", db)
    except Exception:
        return MOCK_WAREHOUSES


@router.post("/racks")
async def get_racks(data: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await call_1c("WMS_GetRacks", db, warehouse=data.get("warehouse", ""))
    except Exception:
        return {"racks": MOCK_SNAPSHOT["racks"]}


@router.post("/occupancy")
async def get_occupancy(data: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await call_1c("WMS_GetOccupancy", db, warehouse=data.get("warehouse", ""))
    except Exception:
        return {"sections": MOCK_SNAPSHOT["sections"], "summary": MOCK_SNAPSHOT["summary"]}


@router.post("/floor")
async def get_floor(data: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await call_1c("WMS_GetFloor", db, warehouse=data.get("warehouse", ""))
    except Exception:
        return {"floorPallets": MOCK_SNAPSHOT["floorPallets"]}


@router.post("/find-cell")
async def find_cell(data: dict, db: AsyncSession = Depends(get_db)):
    return await call_1c("WMS_FindCell", db,
                         warehouse=data.get("warehouse", ""),
                         pallet=data.get("pallet", ""))


@router.post("/validate")
async def validate_placement(data: dict, db: AsyncSession = Depends(get_db)):
    return await call_1c("WMS_ValidatePlacement", db,
                         warehouse=data.get("warehouse", ""),
                         cell=data.get("cell", ""),
                         pallet=data.get("pallet", ""))


@router.post("/move")
async def move_pallet(data: dict, db: AsyncSession = Depends(get_db)):
    return await call_1c("WMS_MovePallet", db,
                         warehouse=data.get("warehouse", ""),
                         pallet=data.get("pallet", ""),
                         targetCell=data.get("targetCell", ""))


@router.post("/snapshot")
async def export_snapshot(data: dict, db: AsyncSession = Depends(get_db)):
    return await call_1c("WMS_ExportSnapshot", db, warehouse=data.get("warehouse", ""))


@router.post("/health")
async def check_connection(db: AsyncSession = Depends(get_db)):
    return await call_1c("WMS_CheckConnection", db)


# --- Optimize (автономный — без 1С) ---

@router.post("/optimize")
async def optimize_placement(req: OptimizeRequest):
    from services.optimizer import place_pallet_batch

    result = place_pallet_batch(req.pallets)
    return result


@router.post("/optimize/floors")
async def optimize_floors(req: FloorsRequest, db: AsyncSession = Depends(get_db)):
    # Прокси в 1С пока — загружаем топологию и занятость, считаем
    racks_data = await call_1c("WMS_GetRacks", db, warehouse=req.warehouse)
    occupancy_data = await call_1c("WMS_GetOccupancy", db, warehouse=req.warehouse)

    from services.floor_optimizer import build_floor_report
    for rack in racks_data.get("racks", []):
        if rack["id"] == req.rackId:
            return build_floor_report(rack, occupancy_data.get("sections", []))

    return {"error": "Rack not found"}


# --- Placements execute ---

@router.post("/placements/execute")
async def execute_placements(data: dict, db: AsyncSession = Depends(get_db)):
    try:
        return await call_1c("WMS_ExecutePlacements", db,
                             warehouse=data.get("warehouse", ""),
                             placements=data.get("placements", []))
    except Exception:
        # MOCK: имитируем успешное выполнение для всех кроме первого
        placements = data.get("placements", [])
        results = []
        for i, p in enumerate(placements):
            if i == 0:
                results.append({"pallet": p.get("pallet"), "ok": True, "document": f"doc-{i}"})
            else:
                results.append({
                    "pallet": p.get("pallet"),
                    "ok": False,
                    "error": f"Address occupied by pallet P-{i:05d}"
                })
        return {
            "ok": True,
            "total": len(placements),
            "moved": min(1, len(placements)),
            "failed": max(0, len(placements) - 1),
            "results": results
        }
