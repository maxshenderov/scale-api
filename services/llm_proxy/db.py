"""
LLM Proxy — Database layer (SQLite via aiosqlite).
Schema: providers, proxy_keys, settings, request_log.
"""

import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path

DB_PATH = "data/proxy.db"


@asynccontextmanager
async def get_db(db_path: str = DB_PATH):
    """Async context manager for database connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
    finally:
        await conn.close()


async def init_db(db_path: str = DB_PATH):
    """Create tables if they don't exist."""
    async with get_db(db_path) as conn:
        await conn.executescript("""
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                path TEXT NOT NULL DEFAULT '/v1/chat/completions',
                format TEXT NOT NULL DEFAULT 'openai' CHECK(format IN ('openai', 'anthropic')),
                port INTEGER NOT NULL DEFAULT 443
            );

            CREATE TABLE IF NOT EXISTS proxy_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                real_key TEXT NOT NULL,
                default_model TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                key_name TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_request_log_timestamp
                ON request_log(timestamp DESC);

            -- Default settings
            INSERT OR IGNORE INTO settings (key, value) VALUES ('override_enabled', '0');
            INSERT OR IGNORE INTO settings (key, value) VALUES ('override_key_id', '');
        """)
        await conn.commit()


# ── Providers CRUD ───────────────────────────────────────────────────────

async def create_provider(conn, name: str, base_url: str, path: str,
                          format: str, port: int = 443) -> int:
    cursor = await conn.execute(
        "INSERT INTO providers (name, base_url, path, format, port) VALUES (?, ?, ?, ?, ?)",
        (name, base_url, path, format, port),
    )
    await conn.commit()
    return cursor.lastrowid


async def get_provider(conn, provider_id: int) -> dict | None:
    cursor = await conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_providers(conn) -> list[dict]:
    cursor = await conn.execute("SELECT * FROM providers ORDER BY name")
    return [dict(row) for row in await cursor.fetchall()]


async def delete_provider(conn, provider_id: int) -> bool:
    cursor = await conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    await conn.commit()
    return cursor.rowcount > 0


# ── Proxy Keys CRUD ──────────────────────────────────────────────────────

async def create_key(conn, name: str, provider_id: int, real_key: str,
                     default_model: str) -> int:
    cursor = await conn.execute(
        "INSERT INTO proxy_keys (name, provider_id, real_key, default_model) VALUES (?, ?, ?, ?)",
        (name, provider_id, real_key, default_model),
    )
    await conn.commit()
    return cursor.lastrowid


async def get_key_by_name(conn, name: str) -> dict | None:
    cursor = await conn.execute(
        """SELECT pk.*, p.name as provider_name, p.base_url, p.path, p.format, p.port
           FROM proxy_keys pk
           JOIN providers p ON pk.provider_id = p.id
           WHERE pk.name = ? AND pk.enabled = 1""",
        (name,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def list_keys(conn) -> list[dict]:
    cursor = await conn.execute(
        """SELECT pk.*, p.name as provider_name
           FROM proxy_keys pk
           JOIN providers p ON pk.provider_id = p.id
           ORDER BY pk.name"""
    )
    return [dict(row) for row in await cursor.fetchall()]


async def delete_key(conn, key_id: int) -> bool:
    cursor = await conn.execute("DELETE FROM proxy_keys WHERE id = ?", (key_id,))
    await conn.commit()
    return cursor.rowcount > 0


async def set_key_enabled(conn, key_id: int, enabled: bool) -> bool:
    cursor = await conn.execute(
        "UPDATE proxy_keys SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, key_id),
    )
    await conn.commit()
    return cursor.rowcount > 0


# ── Settings ─────────────────────────────────────────────────────────────

async def set_setting(conn, key: str, value: str):
    await conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    await conn.commit()


async def get_setting(conn, key: str) -> str | None:
    cursor = await conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else None


async def get_all_settings(conn) -> dict:
    cursor = await conn.execute("SELECT key, value FROM settings")
    return {row["key"]: row["value"] for row in await cursor.fetchall()}


# ── Request Log ──────────────────────────────────────────────────────────

async def log_request(conn, key_name: str, provider: str, model: str,
                      tokens_in: int, tokens_out: int, duration_ms: int,
                      error: str = None):
    await conn.execute(
        """INSERT INTO request_log (key_name, provider, model, tokens_in, tokens_out, duration_ms, error)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key_name, provider, model, tokens_in, tokens_out, duration_ms, error),
    )
    await conn.commit()


async def get_recent_logs(conn, limit: int = 200) -> list[dict]:
    cursor = await conn.execute(
        "SELECT * FROM request_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    )
    return [dict(row) for row in await cursor.fetchall()]


async def clear_logs(conn):
    await conn.execute("DELETE FROM request_log")
    await conn.commit()
