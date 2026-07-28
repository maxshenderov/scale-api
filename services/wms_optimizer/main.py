"""WMS Pallet Optimizer — точка входа FastAPI-приложения."""
import json
import logging
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="WMS Pallet Optimizer",
    description="Python Optimization Engine для WMS — оптимальное размещение паллет (OR-Tools CP-SAT)",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Логируем тело запроса при 422 для отладки."""
    try:
        body = await request.body()
        logger.error("422 Validation Error — BODY: %s", body.decode("utf-8", errors="replace")[:2000])
    except Exception:
        logger.error("422 Validation Error — не удалось прочитать тело")
    logger.error("422 Validation Errors: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# Подключение статических файлов (документация)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(router, prefix="/api", tags=["optimization"])


@app.get("/", include_in_schema=False)
async def root():
    """Главная страница — перенаправление на документацию."""
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    config_path = os.path.join(os.path.dirname(__file__), "config", "settings.json")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    api_cfg = cfg.get("api", {})
    uvicorn.run(
        "main:app",
        host=api_cfg.get("host", "0.0.0.0"),
        port=api_cfg.get("port", 8010),
        workers=api_cfg.get("workers", 1),
        reload=False,
    )
