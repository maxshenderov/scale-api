from fastapi import WebSocket
import json
import logging

logger = logging.getLogger(__name__)


class WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._latest: dict | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info(f"1C client connected. Total: {len(self._connections)}")

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
            logger.info(f"1C client disconnected. Total: {len(self._connections)}")

    async def broadcast(self, msg: dict) -> None:
        self._latest = msg
        data = json.dumps(msg, ensure_ascii=False)
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def connected(self) -> bool:
        return len(self._connections) > 0

    @property
    def latest(self) -> dict | None:
        return self._latest
