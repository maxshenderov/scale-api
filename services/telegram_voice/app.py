import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ws_manager import WSManager
from stt import STT
from bot import VoiceBot
from session import SessionManager
from llm import ask as llm_ask

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("app")

ws_manager = WSManager()
stt = STT()
sessions = SessionManager()
voice_bot = VoiceBot(stt, ws_manager, sessions)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting bot polling...")
    import asyncio
    bot_task = asyncio.create_task(voice_bot.start())
    yield
    logger.info("Shutting down bot...")
    await voice_bot.stop()
    bot_task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ContextRequest(BaseModel):
    session_id: str
    chat_id: int
    form_name: str = ""
    assistant_type: str = "General"
    form_context: dict = {}
    tools: list = []
    system_prompt: str = ""


@app.post("/context")
async def set_context(req: ContextRequest):
    """Принять контекст из 1С при нажатии «Спросить»."""
    ctx = {
        "type": req.assistant_type,
        "form_name": req.form_name,
        "form_context": req.form_context,
        "tools": req.tools,
        "system_prompt": req.system_prompt,
    }
    sessions.create(req.session_id, req.chat_id, ctx)
    logger.info(f"Context: session={req.session_id} chat={req.chat_id} type={req.assistant_type}")
    return {"ok": True}


class TestTextRequest(BaseModel):
    text: str


@app.post("/test-text")
async def test_text(req: TestTextRequest):
    """Тестовый эндпоинт: вставить текст как будто из Telegram (без STT)."""
    msg = {"text": req.text, "type": "ai_response", "timestamp": datetime.now(timezone.utc).isoformat()}
    ws_manager._latest = msg
    await ws_manager.broadcast(msg)
    logger.info(f"Test text injected: {req.text[:80]}")
    return {"ok": True, "text": req.text, "type": "ai_response"}


@app.get("/latest")
async def latest(since: str = ""):
    """Получить последнюю транскрипцию (опрашивается 1С).

    Опциональный query-параметр since (ISO timestamp):
    если передан — вернуть empty, когда сообщение не новее since.
    """
    if not ws_manager.latest:
        return {"text": "", "type": "empty"}
    if since:
        try:
            msg_time = datetime.fromisoformat(ws_manager.latest.get("timestamp", ""))
            since_time = datetime.fromisoformat(since)
            if msg_time <= since_time:
                return {"text": "", "type": "empty", "since": since}
        except (ValueError, TypeError):
            pass  # некорректный формат даты — возвращаем как есть
    return ws_manager.latest


@app.get("/display")
async def display():
    """HTML-страница для ПолеHTML в 1С: автоопрос /latest.

    Фиксирует время загрузки страницы (connectTime) и передаёт в ?since=,
    чтобы не показывать сообщения, пришедшие до открытия формы.
    """
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><meta charset='UTF-8'><style>
body{font-family:sans-serif;font-size:20px;padding:20px;color:#333;margin:0}
#status{font-size:12px;color:#999;margin-bottom:10px}
</style></head>
<body>
<div id="status">Подключено. Ожидание новых сообщений из Telegram...</div>
<div id="text"></div>
<script>
const connectTime = new Date().toISOString();
let lastText = '';
setInterval(async () => {
    try {
        const r = await fetch('/latest?since=' + encodeURIComponent(connectTime));
        const d = await r.json();
        if (d.text && d.text !== lastText) {
            lastText = d.text;
            document.getElementById('text').innerText = d.text;
            document.getElementById('status').innerText = 'Новое: ' + d.text;
        }
    } catch(e) {
        document.getElementById('status').innerText = 'Ошибка: ' + e.message;
    }
}, 1000);
</script>
</body>
</html>
    """)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # держим соединение, читаем keep-alive (1С шлёт ping)
            data = await ws.receive_text()
            logger.debug(f"From 1C: {data}")
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
