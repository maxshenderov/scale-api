from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.connection import get_db
from models.schemas import SnapshotIn
import logging

router = APIRouter(prefix="/api/snapshot")
_logger = logging.getLogger(__name__)


async def safe_db(db):
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.post("/export")
async def export_snapshot(data: dict, db: AsyncSession = Depends(get_db)):
    """Прокси в 1С WMS_ExportSnapshot"""
    from services.wms_client import WmsClient
    result = await db.execute(
        text("SELECT url, login, password FROM connections WHERE is_active = true LIMIT 1")
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=503, detail="No active connection")
    client = WmsClient(base_url=row[0], login=row[1], password=row[2])
    return await client.call("WMS_ExportSnapshot", warehouse=data.get("warehouse", ""))


@router.post("/load")
async def load_snapshot(data: SnapshotIn, db: AsyncSession = Depends(get_db)):
    """Загрузить JSON-снимок для автономной работы"""
    import json
    await db.execute(
        text("UPDATE snapshots SET is_active = false")
    )
    await db.execute(
        text("""INSERT INTO snapshots (name, warehouse_name, data, is_active)
                VALUES (:n, :w, :d::jsonb, true)"""),
        {"n": data.name, "w": data.warehouse_name, "d": json.dumps(data.data)}
    )
    await db.commit()
    return {"ok": True}


@router.post("/list")
async def list_snapshots(db: AsyncSession = Depends(get_db)):
    if not await safe_db(db):
        return {"snapshots": []}
    result = await db.execute(
        text("SELECT id, name, warehouse_name, is_active, created_at FROM snapshots ORDER BY created_at DESC")
    )
    rows = result.fetchall()
    return {
        "snapshots": [
            {"id": r[0], "name": r[1], "warehouse_name": r[2],
             "is_active": r[3], "created_at": str(r[4])}
            for r in rows
        ]
    }


@router.post("/activate/{snap_id}")
async def activate_snapshot(snap_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("UPDATE snapshots SET is_active = false"))
    await db.execute(text("UPDATE snapshots SET is_active = true WHERE id=:id"), {"id": snap_id})
    await db.commit()
    return {"ok": True}


@router.post("/data")
async def get_snapshot_data(data: dict, db: AsyncSession = Depends(get_db)):
    """Получить данные активного снимка для офлайн-работы"""
    snap_id = data.get("id")
    if snap_id:
        result = await db.execute(
            text("SELECT id, name, warehouse_name, data, is_active, created_at FROM snapshots WHERE id=:id"),
            {"id": snap_id}
        )
    else:
        result = await db.execute(
            text("SELECT id, name, warehouse_name, data, is_active, created_at FROM snapshots WHERE is_active = true LIMIT 1")
        )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {
        "id": row[0], "name": row[1], "warehouse_name": row[2],
        "data": row[3], "is_active": row[4], "created_at": str(row[5])
    }
