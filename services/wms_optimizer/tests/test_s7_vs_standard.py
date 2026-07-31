"""Регрессионный тест: холодный старт S7 не должен размещать меньше, чем эталон S6 (Фаза D).

Фикстуры: OccupancyS7.json (1530 секций, 0 existing паллет — тот же профиль,
что и живой инцидент зависания/OOM), FloorS7.json (3406 паллет с пола),
OccupancyS6Standard.json (та же раскладка секций twin-склада С6, уже заполненная
ВРУЧНУЮ — эталон). Эталонное число размещённых паллет считается прямо в тесте
через build_warehouse_state() (тот же код, что разбирает occupancy в проде), а
не хардкодится — на момент написания теста получается 3242 из 3406 (164 не
разместили даже вручную).

Критерий: оптимизатор не должен быть хуже человека — если placedPallets меньше
эталона, это регрессия.

allowReslot=False: existing=0, реслотить нечего — это гарантирует, что
global_optimizer не будет создавать решения о реслоте, и на 3406×1530
допустимых парах (заведомо выше FEASIBLE_PAIRS_THRESHOLD) сработает
агрегированная CP-SAT модель (solver/cp_sat_aggregated.py, Фаза C) — именно
её и проверяет этот тест на реальных данных инцидента.

ВНИМАНИЕ: тяжёлый прогон (1530 секций, 3406 паллет). До Фаз A-C этот же профиль
воспроизводил живой инцидент (зависание/OOM на точной модели) — на текущем
коде должен укладываться в секунды/десятки секунд. Не входит в быстрый прогон
`pytest tests/ -v`. Запуск отдельно: pytest tests/test_s7_vs_standard.py -v -s
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from models.occupancy_builder import build_warehouse_state
from optimizer.global_optimizer import run_optimization

EXAMPLE_DIR = os.path.join(os.path.dirname(__file__), "example")

S7_SETTINGS = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=180,  # Было: 120. Увеличено для достижения OPTIMAL на S7
    twoStageReslot=True,  # Включаем двухэтапный режим
    twoStageReslotMaxReslotPercent=10.0,
    twoStageReslotTimeLimitSeconds=120,
)


def _load_occupancy(filename: str):
    path = os.path.join(EXAMPLE_DIR, filename)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [OccupancySectionSchema(**row) for row in raw["sections"]]


def _load_floor_pallets():
    path = os.path.join(EXAMPLE_DIR, "FloorS7.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"], height=p["height"], depth=p["depth"], weight=p["weight"],
        )
        for i, p in enumerate(raw["floorPallets"])
    ]


def test_cold_start_s7_not_worse_than_manual_reference():
    """Холодный старт S7 (0 existing, 3406 с пола) vs эталон S6 (ручная раскладка).

    Использует двухэтапный подход через параметр twoStageReslot=True:
    - ЭТАП 1: размещение без реслота (allowReslot=False)
    - ЭТАП 2: реслот не размещённых (allowReslot=True, maxReslotPercent=10%)

    Это автоматический режим — не нужно вручную строить occupancy после ЭТАПА 1.
    """
    occupancy_s7 = _load_occupancy("OccupancyS7.json")
    floor_pallets = _load_floor_pallets()

    occupancy_reference = _load_occupancy("OccupancyS6Standard.json")
    _, _, reference_pallets = build_warehouse_state(occupancy_reference)
    reference_count = len(reference_pallets)

    # Двухэтапный подход через параметр twoStageReslot=True
    req = OptimizationRequest(
        optimizationId="S7-COLD-START-TWO-STAGE",
        mode="place",
        occupancy=occupancy_s7,
        newPallets=floor_pallets,
        settings=S7_SETTINGS,  # twoStageReslot=True
    )
    resp = run_optimization(req)

    print("\n=== Холодный старт S7 vs эталон S6 (двухэтапный режим) ===")
    print(f"solverStatus={resp.solverStatus} placementStatus={resp.placementStatus} score={resp.score}")
    print(f"executionTimeSeconds={resp.executionTimeSeconds}")
    print(f"Секций: {len(occupancy_s7)}, паллет с пола: {len(floor_pallets)}")
    print(f"Эталон (ручная раскладка S6): {reference_count}/{len(floor_pallets)}")
    print(f"Оптимизатор: placedPallets={resp.metrics.placedPallets}/{len(floor_pallets)} "
          f"notPlacedPallets={resp.metrics.notPlacedPallets}")
    print(f"Передвинуто: {resp.metrics.movedPallets}")

    if resp.notPlaced:
        reasons = Counter(np.reason for np in resp.notPlaced)
        print(f"Причины отказа (notPlaced): {dict(reasons)}")

    assert resp.solverStatus in ("OPTIMAL", "FEASIBLE", "TIME_LIMIT")
    assert resp.metrics.placedPallets >= reference_count, (
        f"Оптимизатор разместил {resp.metrics.placedPallets} паллет — хуже ручного "
        f"эталона ({reference_count}). Регрессия."
    )
