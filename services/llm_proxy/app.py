"""
LLM Proxy — FastAPI server.

Endpoints:
  /health                    health check
  /v1/chat/completions       1C → proxy → real provider
  /v1/models                 list models from default provider
  /ws                        WebSocket (future 1C integration)
  /api/providers             CRUD for providers (used by UI)
  /api/keys                  CRUD for proxy keys (used by UI)
  /api/settings              get/set settings (used by UI)
  /api/logs                  request logs (used by UI)
  /ui                        SPA static files
"""

import json
import time
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from db import (
    init_db, get_db,
    create_provider, get_provider, list_providers, delete_provider,
    create_key, get_key_by_name, list_keys, delete_key, set_key_enabled,
    set_setting, get_setting, get_all_settings,
    log_request, get_recent_logs, clear_logs,
    check_admin_password, is_admin_password_set, set_admin_password,
    get_enabled_models, refresh_models, update_models, get_models_by_provider,
)
from translator import openai_to_anthropic, anthropic_to_openai

# ── Config ───────────────────────────────────────────────────────────────

ANTHROPIC_VERSION = "2023-06-01"
HTTP_TIMEOUT = 240.0
DB_PATH = "data/proxy.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("llm_proxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(DB_PATH)
    yield


app = FastAPI(title="LLM Proxy", lifespan=lifespan)

# Static files for web UI
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Admin verification dependency ──────────────────────────────────────────

async def verify_admin(x_admin_key: str = Header(default="")):
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="Missing X-Admin-Key header")
    async with get_db(DB_PATH) as conn:
        if not await check_admin_password(conn, x_admin_key):
            raise HTTPException(status_code=403, detail="Invalid admin password")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/ui")
async def ui():
    return FileResponse("static/index.html")


# ═══════════════════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════════════════
# Models list (proxy to default provider or override provider)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/models")
async def list_models():
    """Return list of enabled proxy connections as models for 1C."""
    async with get_db(DB_PATH) as conn:
        keys = await list_keys(conn)
        if not keys:
            raise HTTPException(status_code=503, detail="No connections configured")

        enabled = [k for k in keys if k.get("enabled")]
        return {
            "data": [
                {
                    "id": k["name"],
                    "name": k.get("display_name") or k["name"],
                    "description": f"{k['provider_name']} | {k['default_model'] or 'авто'}"
                }
                for k in enabled
            ]
        }


@app.get("/api/v1/models")
async def list_models_api():
    """Alias for /v1/models — 1C compatibility."""
    return await list_models()


async def _get_key_by_id(conn, key_id: int) -> dict | None:
    import aiosqlite
    cursor = await conn.execute(
        """SELECT pk.*, p.name as provider_name, p.base_url, p.path, p.format, p.port
           FROM proxy_keys pk JOIN providers p ON pk.provider_id = p.id
           WHERE pk.id = ? AND pk.enabled = 1""",
        (key_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def _fetch_models(key_info: dict) -> dict:
    """Fetch /v1/models from the real provider."""
    base_url = key_info["base_url"]
    port = key_info["port"]
    real_key = key_info["real_key"]
    path = key_info.get("path", "")
    provider_format = key_info.get("format", "openai")
    scheme = "https" if port == 443 else "http"
    # Models endpoint: for anthropic derive from path, for openai use standard /v1/models
    if provider_format == "anthropic":
        path_parts = path.rsplit("/", 1)
        models_path = path_parts[0] + "/models" if len(path_parts) > 1 else "/models"
    else:
        models_path = "/api/v1/models"
    url = f"{scheme}://{base_url}{models_path}"

    headers = {"Authorization": f"Bearer {real_key}"}
    if provider_format == "anthropic":
        headers["x-api-key"] = real_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Provider models error: {resp.status_code}")
        return resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# Main proxy: Chat Completions
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI Chat Completions → translate if needed → real provider → translate back.
    1C sends: Authorization: Bearer <key_name>
    """
    body = await request.json()

    # Extract key name from Authorization header, fallback to model
    auth = request.headers.get("Authorization", "")
    key_name = auth.replace("Bearer", "", 1).strip()
    if not key_name:
        key_name = body.get("model", "").strip()

    if not key_name:
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <key_name>")

    start_time = time.time()

    async with get_db(DB_PATH) as conn:
        # Find the proxy key
        key_info = await get_key_by_name(conn, key_name)
        if not key_info:
            raise HTTPException(status_code=401, detail=f"Unknown or disabled key: {key_name}")

        # Check override
        override_enabled = await get_setting(conn, "override_enabled")
        if override_enabled == "1":
            override_key_id = await get_setting(conn, "override_key_id")
            if override_key_id:
                override_info = await _get_key_by_id(conn, int(override_key_id))
                if override_info:
                    key_info = override_info

        # Determine model: if body model matches ANY key name, use key's default_model
        model = body.get("model", "") or key_info.get("default_model", "")
        if key_info.get("default_model"):
            model = key_info["default_model"]
        provider_format = key_info["format"]  # "anthropic" or "openai"
        base_url = key_info["base_url"]
        path = key_info["path"]
        port = key_info["port"]
        real_key = key_info["real_key"]
        provider_name = key_info.get("provider_name", "unknown")

        # Translate if needed
        if provider_format == "anthropic":
            request_body = openai_to_anthropic(body)
        else:
            request_body = body

        # Always set the real model from the key's default_model
        if key_info.get("default_model"):
            request_body["model"] = key_info["default_model"]
        elif "model" not in request_body or not request_body.get("model"):
            request_body["model"] = model

        # Build real URL
        scheme = "https" if port == 443 else "http"
        url = f"{scheme}://{base_url}{path}"

        # Headers for real provider
        headers = {"Content-Type": "application/json"}
        if provider_format == "anthropic":
            headers["x-api-key"] = real_key
            headers["anthropic-version"] = ANTHROPIC_VERSION
        else:
            headers["Authorization"] = f"Bearer {real_key}"

        logger.info("→ %s [%s] model=%s msgs=%d", provider_name, provider_format, model, len(body.get("messages", [])))

        # Send to real provider
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, verify=False) as client:
                resp = await client.post(url, json=request_body, headers=headers)

            if resp.status_code != 200:
                error_body = resp.text[:500]
                logger.error("Provider error %d: %s", resp.status_code, error_body)
                duration_ms = int((time.time() - start_time) * 1000)
                await log_request(conn, key_name, provider_name, model, 0, 0, duration_ms, error_body)
                raise HTTPException(status_code=502, detail=f"Provider: {resp.status_code} — {error_body[:200]}")

            resp_data = resp.json()

            # Translate response back
            if provider_format == "anthropic":
                result = anthropic_to_openai(resp_data, model)
            else:
                result = resp_data

            duration_ms = int((time.time() - start_time) * 1000)
            usage = result.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

            logger.info("← %s tokens_in=%d tokens_out=%d %dms", provider_name, tokens_in, tokens_out, duration_ms)

            await log_request(conn, key_name, provider_name, model, tokens_in, tokens_out, duration_ms)

            return result

        except httpx.TimeoutException:
            duration_ms = int((time.time() - start_time) * 1000)
            await log_request(conn, key_name, provider_name, model, 0, 0, duration_ms, error="timeout")
            raise HTTPException(status_code=504, detail="Provider timeout")
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            await log_request(conn, key_name, provider_name, model, 0, 0, duration_ms, error=str(e))
            raise


# ═══════════════════════════════════════════════════════════════════════════
# WebSocket (future)
# ═══════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    # TODO: implement when 1C webSocketСоединение is ready
    # For now — echo for testing
    try:
        while True:
            data = await ws.receive_text()
            await ws.send_text(json.dumps({"type": "echo", "data": json.loads(data)}))
    except WebSocketDisconnect:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Auth API (admin password management)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/auth/status")
async def auth_status():
    async with get_db(DB_PATH) as conn:
        pw_set = await is_admin_password_set(conn)
    return {"password_set": pw_set}


@app.post("/api/auth/setup")
async def auth_setup(data: dict):
    async with get_db(DB_PATH) as conn:
        if await is_admin_password_set(conn):
            raise HTTPException(status_code=403, detail="Password already set")
        pw = data.get("password", "").strip()
        if len(pw) < 4:
            raise HTTPException(status_code=400, detail="Password too short (min 4 chars)")
        await set_admin_password(conn, pw)
    return {"ok": True}


@app.post("/api/auth/login")
async def auth_login(data: dict, x_admin_key: str = Header(default="")):
    pw = x_admin_key or data.get("password", "")
    async with get_db(DB_PATH) as conn:
        if await check_admin_password(conn, pw):
            return {"ok": True}
    raise HTTPException(status_code=403, detail="Invalid password")


# ═══════════════════════════════════════════════════════════════════════════
# API for Web UI (providers, keys, settings, logs)
# ═══════════════════════════════════════════════════════════════════════════

# ── Providers ────────────────────────────────────────────────────────────

@app.get("/api/providers")
async def api_list_providers():
    async with get_db(DB_PATH) as conn:
        return await list_providers(conn)


@app.post("/api/providers")
async def api_create_provider(data: dict, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        pid = await create_provider(
            conn,
            name=data["name"],
            base_url=data["base_url"],
            path=data.get("path", "/v1/chat/completions"),
            format=data.get("format", "openai"),
            port=data.get("port", 443),
        )
        return {"id": pid}


@app.delete("/api/providers/{provider_id}")
async def api_delete_provider(provider_id: int, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        ok = await delete_provider(conn, provider_id)
        if not ok:
            raise HTTPException(status_code=404)
        return {"ok": True}


# ── Provider Models ───────────────────────────────────────────────────────

@app.get("/api/providers/{provider_id}/models")
async def api_get_models(provider_id: int):
    async with get_db(DB_PATH) as conn:
        return await get_models_by_provider(conn, provider_id)


@app.post("/api/providers/{provider_id}/models/refresh")
async def api_refresh_models(provider_id: int, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        prov = await get_provider(conn, provider_id)
        if not prov:
            raise HTTPException(status_code=404, detail="Provider not found")
        # Find a key for THIS provider
        keys = await list_keys(conn)
        matching_key = next((k for k in keys if k["provider_id"] == provider_id), None)
        if not matching_key:
            raise HTTPException(status_code=400, detail="No key configured for this provider. Create one first.")
        # Build full key_info with provider fields
        key_info = {
            **matching_key,
            "base_url": prov["base_url"],
            "path": prov["path"],
            "format": prov["format"],
            "port": prov["port"],
        }
        raw = await _fetch_models(key_info)
        models_list = raw.get("data", raw) if isinstance(raw, dict) else raw
        parsed = []
        for m in models_list:
            if isinstance(m, dict):
                parsed.append({
                    "id": m.get("id", m.get("name", "")),
                    "description": m.get("description", m.get("name", "")),
                })
        count = await refresh_models(conn, provider_id, parsed)
    return {"ok": True, "count": count}


@app.put("/api/providers/{provider_id}/models")
async def api_update_models(provider_id: int, data: list[dict], _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        count = await update_models(conn, provider_id, data)
    return {"ok": True, "count": count}


# ── Proxy Keys ───────────────────────────────────────────────────────────

@app.get("/api/keys")
async def api_list_keys():
    async with get_db(DB_PATH) as conn:
        return await list_keys(conn)


@app.post("/api/keys")
async def api_create_key(data: dict, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        kid = await create_key(
            conn,
            name=data["name"],
            provider_id=data["provider_id"],
            real_key=data["real_key"],
            default_model=data.get("default_model", ""),
        )
        return {"id": kid}


@app.put("/api/keys/{key_id}")
async def api_update_key(key_id: int, data: dict, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        for field in ["default_model", "display_name", "name"]:
            if field in data and data[field] is not None:
                await conn.execute(
                    f"UPDATE proxy_keys SET {field} = ? WHERE id = ?",
                    (data[field], key_id),
                )
        await conn.commit()
        return {"ok": True}


@app.delete("/api/keys/{key_id}")
async def api_delete_key(key_id: int, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        ok = await delete_key(conn, key_id)
        if not ok:
            raise HTTPException(status_code=404)
        return {"ok": True}


@app.post("/api/keys/{key_id}/toggle")
async def api_toggle_key(key_id: int, data: dict, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        ok = await set_key_enabled(conn, key_id, data.get("enabled", True))
        if not ok:
            raise HTTPException(status_code=404)
        return {"ok": True}


# ── Settings ─────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_get_settings():
    async with get_db(DB_PATH) as conn:
        return await get_all_settings(conn)


@app.post("/api/settings")
async def api_set_settings(data: dict, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        normalized = dict(data)
        if 'override_enabled' in normalized:
            val = normalized['override_enabled']
            if val is True or val == 'true' or val == 'True' or val == '1':
                normalized['override_enabled'] = '1'
            else:
                normalized['override_enabled'] = '0'
        for key, value in normalized.items():
            await set_setting(conn, key, str(value))
        return {"ok": True}


# ── Logs ─────────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def api_get_logs(limit: int = 200):
    async with get_db(DB_PATH) as conn:
        return await get_recent_logs(conn, limit)


@app.delete("/api/logs")
async def api_clear_logs(_: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        await clear_logs(conn)
        return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
