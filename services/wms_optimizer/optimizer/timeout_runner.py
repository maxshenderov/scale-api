"""Wall-clock таймаут на весь run_optimization() (Фаза A).

solver.parameters.max_time_in_seconds ограничивает только CP-SAT solver.Solve() —
построение модели (feasibility, BoolVar/IntVar, ограничения) выполняется чистым
Python и ничем не ограничено. На холодном складе с большим числом допустимых
пар это может уйти в неограниченный по времени/памяти рост ещё до вызова
solver.Solve(). Поток (threading/asyncio) здесь не поможет — зависший native
C++ код ortools не отвечает на кооперативную отмену; принудительно освободить
CPU/память может только process.kill() отдельного процесса.
"""
from __future__ import annotations

import logging
import multiprocessing as mp

from api.schemas import OptimizationRequest, OptimizationResponse
from optimizer.global_optimizer import run_optimization
from solver.cp_sat_aggregated import _RESIDUAL_TIME_LIMIT_SECONDS, _RESLOT_TIME_LIMIT_SECONDS

logger = logging.getLogger(__name__)

# Запас сверх settings.timeLimitSeconds — покрывает построение модели вне
# solver.Solve() (см. docstring выше), которое settings.timeLimitSeconds не ограничивает.
TIMEOUT_MARGIN_SECONDS = 60

# Агрегированная модель (solver/cp_sat_aggregated.py) после основного Solve()
# сама запускает до двух дополнительных CP-SAT проходов над остатком —
# _resolve_residual_exact (_RESIDUAL_TIME_LIMIT_SECONDS) и, если там кто-то
# остался, _resolve_residual_with_reslot (_RESLOT_TIME_LIMIT_SECONDS). Эти
# проходы идут ПОСЛЕ settings.timeLimitSeconds и не ограничены им — без
# отдельного запаса process.kill() срабатывает раньше, чем модель успевает
# закончить свою же внутреннюю логику (наблюдалось на S7: 180s solve + 60s
# reslot-дорешивание = 240s против таймаута 180+60=240s — впритык до сбоя).
AGGREGATED_RESIDUAL_MARGIN_SECONDS = _RESIDUAL_TIME_LIMIT_SECONDS + _RESLOT_TIME_LIMIT_SECONDS

# Даём убитому процессу немного времени на фактическое завершение перед join.
KILL_GRACE_SECONDS = 5


class OptimizationTimeoutError(Exception):
    def __init__(self, optimization_id: str, timeout: float):
        self.optimization_id = optimization_id
        self.timeout = timeout
        super().__init__(f"Оптимизация {optimization_id} превысила wall-clock таймаут {timeout:.0f}с")


def _target(req: OptimizationRequest, conn) -> None:
    try:
        result = run_optimization(req)
        conn.send(("ok", result))
    except Exception as e:  # noqa: BLE001 — пробрасываем любую ошибку в родительский процесс
        conn.send(("error", str(e)))
    finally:
        conn.close()


def run_optimization_with_timeout(req: OptimizationRequest) -> OptimizationResponse:
    """Как run_optimization(), но в отдельном процессе с жёстким wall-clock таймаутом.

    При превышении таймаута процесс принудительно убивается и вместо результата
    выбрасывается OptimizationTimeoutError — вызывающий код (api/routes.py)
    отвечает клиенту понятной ошибкой вместо бесконечного зависания.
    """
    # Для двухэтапного режима суммируем оба этапа; каждый этап — это отдельный
    # вызов агрегированной модели, поэтому запас на остаточные проходы (см.
    # AGGREGATED_RESIDUAL_MARGIN_SECONDS) добавляем один раз на весь запрос —
    # ЭТАП 1 и ЭТАП 2 не работают параллельно, их внутренние довычисления не
    # накладываются друг на друга по времени.
    if req.settings.twoStageReslot:
        total_time = req.settings.timeLimitSeconds + req.settings.twoStageReslotTimeLimitSeconds
    else:
        total_time = req.settings.timeLimitSeconds

    timeout = total_time + TIMEOUT_MARGIN_SECONDS + AGGREGATED_RESIDUAL_MARGIN_SECONDS

    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_target, args=(req, child_conn), daemon=True)
    process.start()
    child_conn.close()

    try:
        if not parent_conn.poll(timeout):
            process.kill()
            process.join(KILL_GRACE_SECONDS)
            logger.error(
                "Оптимизация id=%s превысила wall-clock таймаут %.0fс — процесс убит",
                req.optimizationId, timeout,
            )
            raise OptimizationTimeoutError(req.optimizationId, timeout)

        status, payload = parent_conn.recv()
    finally:
        parent_conn.close()

    process.join()

    if status == "error":
        raise RuntimeError(payload)
    return payload
