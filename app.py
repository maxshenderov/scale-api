"""Scale API — HTTP-сервис для чтения показаний весов СКУ I2121 (СКИ-12/Yaohua)."""
import logging
import os

import uvicorn
from fastapi import FastAPI

from scale_reader import read_weight

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Scale API",
    description="HTTP-сервис для чтения показаний весов через M2M WiFi-модуль (TCP)",
    version="1.0.0",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/weight")
async def get_weight():
    """Прочитать одно показание с весов."""
    try:
        w = read_weight()
    except Exception as e:
        logger.error("Scale read error: %s", e)
        return {"ok": False, "value": None, "detail": str(e)}

    if w is None:
        return {"ok": False, "value": None, "detail": "no reading"}

    return {
        "ok": True,
        "value": w.value,
        "unit": w.unit,
        "stable": w.stable,
        "mode": w.mode,
        "raw": w.raw,
    }


if __name__ == "__main__":
    port = int(os.getenv("SERVER_PORT", "8011"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
