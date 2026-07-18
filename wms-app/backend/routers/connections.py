from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from db.connection import get_db
from services.wms_client import WmsClient
import logging

router = APIRouter(prefix="/api/connections")
_logger = logging.getLogger(__name__)


async def safe_db(db):
    """Проверяет доступность БД. Если нет — возвращает пустые списки."""
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("")
async def list_connections(db: AsyncSession = Depends(get_db)):
    if not await safe_db(db):
        return {"connections": []}
    result = await db.execute(
        text("SELECT id, name, url, login, password, is_active FROM connections ORDER BY id")
    )
    rows = result.fetchall()
    return {
        "connections": [
            {"id": r[0], "name": r[1], "url": r[2], "login": r[3],
             "password": r[4], "is_active": r[5]}
            for r in rows
        ]
    }


@router.post("")
async def create_connection(data: dict, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("INSERT INTO connections (name, url, login, password) VALUES (:n, :u, :l, :p)"),
        {"n": data["name"], "u": data["url"], "l": data.get("login", ""), "p": data.get("password", "")}
    )
    await db.commit()
    return {"ok": True}


@router.put("/{conn_id}")
async def update_connection(conn_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("UPDATE connections SET name=:n, url=:u, login=:l, password=:p WHERE id=:id"),
        {"n": data["name"], "u": data["url"], "l": data.get("login", ""),
         "p": data.get("password", ""), "id": conn_id}
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{conn_id}")
async def delete_connection(conn_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM connections WHERE id=:id"), {"id": conn_id})
    await db.commit()
    return {"ok": True}


@router.post("/{conn_id}/activate")
async def activate_connection(conn_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("UPDATE connections SET is_active = false"))
    await db.execute(text("UPDATE connections SET is_active = true WHERE id=:id"), {"id": conn_id})
    await db.commit()
    return {"ok": True}


@router.post("/{conn_id}/test")
async def test_connection(conn_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT url, login, password FROM connections WHERE id=:id"),
        {"id": conn_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Connection not found")

    client = WmsClient(base_url=row[0], login=row[1], password=row[2])
    try:
        resp = await client.call("WMS_CheckConnection")
        return resp
    except Exception as e:
        return {"ok": False, "error": str(e)}
