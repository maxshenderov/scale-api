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
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from db import (
    init_db, get_db,
    create_provider, get_provider, list_providers, delete_provider,
    create_key, get_key_by_name, list_keys, delete_key, set_key_enabled,
    set_setting, get_setting, get_all_settings,
    log_request, get_recent_logs, clear_logs,
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
    """Fetch model list from the default (or override) provider."""
    async with get_db(DB_PATH) as conn:
        # Determine which key to use
        override_enabled = await get_setting(conn, "override_enabled")
        override_key_id = await get_setting(conn, "override_key_id")

        if override_enabled == "1" and override_key_id:
            key_info = await _get_key_by_id(conn, int(override_key_id))
        else:
            # Use first enabled key
            keys = await list_keys(conn)
            if keys:
                key_info = await _get_key_by_id(conn, keys[0]["id"])
            else:
                key_info = None

        if not key_info:
            raise HTTPException(status_code=503, detail="No proxy keys configured")

        return await _fetch_models(key_info)


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
    # Derive models URL from provider path (strip last segment, add /models)
    path_parts = path.rsplit("/", 1)
    models_path = path_parts[0] + "/models" if len(path_parts) > 1 else "/v1/models"
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

    # Extract key name from Authorization header
    auth = request.headers.get("Authorization", "")
    key_name = auth.replace("Bearer ", "", 1).strip()

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

        # Determine model
        model = body.get("model", "") or key_info.get("default_model", "")
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

        # Ensure model is set
        if "model" not in request_body or not request_body["model"]:
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
# API for Web UI (providers, keys, settings, logs)
# ═══════════════════════════════════════════════════════════════════════════

# ── Providers ────────────────────────────────────────────────────────────

@app.get("/api/providers")
async def api_list_providers():
    async with get_db(DB_PATH) as conn:
        return await list_providers(conn)


@app.post("/api/providers")
async def api_create_provider(data: dict):
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
async def api_delete_provider(provider_id: int):
    async with get_db(DB_PATH) as conn:
        ok = await delete_provider(conn, provider_id)
        if not ok:
            raise HTTPException(status_code=404)
        return {"ok": True}


# ── Proxy Keys ───────────────────────────────────────────────────────────

@app.get("/api/keys")
async def api_list_keys():
    async with get_db(DB_PATH) as conn:
        return await list_keys(conn)


@app.post("/api/keys")
async def api_create_key(data: dict):
    async with get_db(DB_PATH) as conn:
        kid = await create_key(
            conn,
            name=data["name"],
            provider_id=data["provider_id"],
            real_key=data["real_key"],
            default_model=data.get("default_model", ""),
        )
        return {"id": kid}


@app.delete("/api/keys/{key_id}")
async def api_delete_key(key_id: int):
    async with get_db(DB_PATH) as conn:
        ok = await delete_key(conn, key_id)
        if not ok:
            raise HTTPException(status_code=404)
        return {"ok": True}


@app.post("/api/keys/{key_id}/toggle")
async def api_toggle_key(key_id: int, data: dict):
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
async def api_set_settings(data: dict):
    async with get_db(DB_PATH) as conn:
        for key, value in data.items():
            await set_setting(conn, key, str(value))
        return {"ok": True}


# ── Logs ─────────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def api_get_logs(limit: int = 200):
    async with get_db(DB_PATH) as conn:
        return await get_recent_logs(conn, limit)


@app.delete("/api/logs")
async def api_clear_logs():
    async with get_db(DB_PATH) as conn:
        await clear_logs(conn)
        return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
