"""FastAPI роуты: синхронный и асинхронный режимы оптимизации (§13 ТЗ)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.schemas import (
    AsyncJobResponse, AsyncJobResultResponse, JobStatus,
    OptimizationRequest, OptimizationResponse,
    PackSectionRequest, PackSectionResponse,
)
from optimizer.timeout_runner import OptimizationTimeoutError, run_optimization_with_timeout
from optimizer.section_packer import optimize_section_fill
from validation.validator import ValidationError, validate_request

router = APIRouter()
logger = logging.getLogger(__name__)

# Хранилище асинхронных задач: optimizationId -> {status, result, error}
_jobs: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Синхронный режим
# ---------------------------------------------------------------------------

@router.post("/optimize", response_model=OptimizationResponse, summary="Синхронная оптимизация")
async def optimize_sync(req: OptimizationRequest) -> OptimizationResponse:
    """Синхронный запуск оптимизации.

    Для небольших задач (< 500 паллет, < 120с). Блокирует до завершения расчёта.
    """
    logger.info(
        "optimize_sync start: id=%s mode=%s new_pallets=%d occupancy_sections=%d",
        req.optimizationId,
        req.mode,
        len(req.newPallets),
        len(req.occupancy),
    )
    try:
        validate_request(req)
    except ValidationError as e:
        logger.error("INVALID_DATA: %s", e.details)
        raise HTTPException(status_code=422, detail={"status": "FAILED", "reason": e.reason, "details": e.details})

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, run_optimization_with_timeout, req)
    except OptimizationTimeoutError as e:
        logger.error("Таймаут оптимизации: %s", e)
        raise HTTPException(status_code=504, detail={"status": "FAILED", "reason": "TIMEOUT", "details": str(e)})
    except Exception as e:
        logger.exception("Ошибка оптимизации: %s", e)
        raise HTTPException(status_code=500, detail={"status": "FAILED", "reason": str(e)})

    logger.info(
        "optimize_sync done: id=%s solver=%s placement=%s score=%.0f time=%.2fs",
        result.optimizationId,
        result.solverStatus,
        result.placementStatus,
        result.score,
        result.executionTimeSeconds,
    )
    return result


# ---------------------------------------------------------------------------
# Асинхронный режим
# ---------------------------------------------------------------------------

@router.post(
    "/optimize/async",
    response_model=AsyncJobResponse,
    status_code=202,
    summary="Асинхронный запуск оптимизации",
)
async def optimize_async_start(req: OptimizationRequest, background_tasks: BackgroundTasks) -> AsyncJobResponse:
    """Асинхронный запуск: возвращает optimizationId немедленно, расчёт идёт в фоне."""
    try:
        validate_request(req)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail={"status": "FAILED", "reason": e.reason, "details": e.details})

    job_id = req.optimizationId
    _jobs[job_id] = {"status": JobStatus.PENDING, "result": None, "error": None, "progress": 0}

    background_tasks.add_task(_run_job, req)
    return AsyncJobResponse(optimizationId=job_id, status=JobStatus.PENDING, progress=0)


@router.get(
    "/optimization/{optimization_id}",
    response_model=AsyncJobResponse,
    summary="Статус асинхронной задачи",
)
async def get_job_status(optimization_id: str) -> AsyncJobResponse:
    job = _jobs.get(optimization_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Задача '{optimization_id}' не найдена")
    return AsyncJobResponse(
        optimizationId=optimization_id,
        status=job["status"],
        progress=job.get("progress", 0),
    )


@router.get(
    "/optimization/{optimization_id}/result",
    response_model=AsyncJobResultResponse,
    summary="Результат асинхронной задачи",
)
async def get_job_result(optimization_id: str) -> AsyncJobResultResponse:
    job = _jobs.get(optimization_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Задача '{optimization_id}' не найдена")
    if job["status"] == JobStatus.RUNNING or job["status"] == JobStatus.PENDING:
        raise HTTPException(status_code=202, detail="Расчёт ещё не завершён")
    if job["status"] == JobStatus.FAILED:
        raise HTTPException(status_code=500, detail={"status": "FAILED", "reason": job.get("error")})
    return job["result"]


# ---------------------------------------------------------------------------
# Вспомогательные
# ---------------------------------------------------------------------------

async def _run_job(req: OptimizationRequest) -> None:
    job_id = req.optimizationId
    _jobs[job_id]["status"] = JobStatus.RUNNING
    _jobs[job_id]["progress"] = 10
    try:
        result = await asyncio.get_event_loop().run_in_executor(None, run_optimization_with_timeout, req)
        _jobs[job_id]["status"] = JobStatus.COMPLETED
        _jobs[job_id]["result"] = result
        _jobs[job_id]["progress"] = 100
        logger.info(
            "optimize_async done: id=%s solver=%s placement=%s score=%.0f time=%.2fs",
            result.optimizationId, result.solverStatus, result.placementStatus,
            result.score, result.executionTimeSeconds,
        )
    except OptimizationTimeoutError as e:
        logger.error("Таймаут фоновой оптимизации %s: %s", job_id, e)
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = f"TIMEOUT: {e}"
    except Exception as e:
        logger.exception("Ошибка фоновой оптимизации %s: %s", job_id, e)
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(e)


# ---------------------------------------------------------------------------
# Section Packing (локальная оптимизация одной секции)
# ---------------------------------------------------------------------------

@router.post("/pack_section", response_model=PackSectionResponse, summary="Оптимальное заполнение секции")
async def pack_section(req: PackSectionRequest) -> PackSectionResponse:
    """
    Локальная оптимизация заполнения одной секции.

    Для жадного алгоритма в 1С: подобрали секцию для первой паллеты,
    теперь оптимально заполнить её из оставшихся типоразмеров.

    **Алгоритм:** жадный с look-ahead (проверка "можно ли остаток заполнить лучше").

    **Вход:**
    - section: физические ограничения секции (ширина, высота, вес, узкопроходность)
    - availableTypes: массив типоразмеров с остатками

    **Выход:**
    - selected: какие типоразмеры взять и сколько штук
    - usedWidth/usedPallets/usedWeight/utilization: метрики размещения
    """
    logger.info(
        "pack_section: section_width=%.0f available_types=%d total_pallets=%d",
        req.section.width,
        len(req.availableTypes),
        sum(t.count for t in req.availableTypes),
    )

    # Конвертация Pydantic → dict для алгоритма
    section_dict = req.section.model_dump()
    types_dict = [t.model_dump() for t in req.availableTypes]

    try:
        selected = optimize_section_fill(section_dict, types_dict, gap_width=50.0)
    except Exception as e:
        logger.exception("Ошибка pack_section: %s", e)
        raise HTTPException(status_code=500, detail={"status": "FAILED", "reason": str(e)})

    # Подсчёт метрик
    used_width = 50.0  # начальный зазор
    used_pallets = 0
    used_weight = 0.0

    for sel in selected:
        t = req.availableTypes[sel["typeIndex"]]
        count = sel["count"]
        used_width += t.width * count + 50.0 * count
        used_pallets += count
        used_weight += t.weight * count

    utilization = (used_width - 50.0) / req.section.width if req.section.width > 0 else 0.0

    logger.info(
        "pack_section done: selected_types=%d used_pallets=%d utilization=%.2f%%",
        len(selected),
        used_pallets,
        utilization * 100,
    )

    return PackSectionResponse(
        selected=selected,
        usedWidth=used_width,
        usedPallets=used_pallets,
        usedWeight=used_weight,
        utilization=utilization,
    )
