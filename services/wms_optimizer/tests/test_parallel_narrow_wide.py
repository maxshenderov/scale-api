"""Тест параллельного подхода: узкопроходные + широкие секции раздельно.

ЭКСПЕРИМЕНТАЛЬНЫЙ тест — проверяет идею разделения задачи на две независимые:
1. ЗАДАЧА 1: Узкопроходные паллеты → узкопроходные секции
2. ЗАДАЧА 2: Широкие паллеты → широкие секции
3. ЗАДАЧА 3 (реслот): Не размещённые из обеих задач → общий реслот

Работает ТОЛЬКО при strictNarrowAislePlacement=True.

Ожидаемый результат:
- Время: ~120s + ~90s + ~10s = 220s (vs 253s двухэтапный)
- Качество: ~3300-3350/3406 (гипотеза)
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


def test_parallel_narrow_wide_vs_two_stage():
    """Параллельный подход (узкопроходные + широкие) vs двухэтапный."""
    occupancy_s7 = _load_occupancy("OccupancyS7.json")
    floor_pallets = _load_floor_pallets()

    occupancy_reference = _load_occupancy("OccupancyS6Standard.json")
    _, _, reference_pallets = build_warehouse_state(occupancy_reference)
    reference_count = len(reference_pallets)

    print("\n=== ПАРАЛЛЕЛЬНЫЙ ПОДХОД: узкопроходные + широкие ===")

    # Разделить паллеты
    narrow_pallets = [p for p in floor_pallets
                     if p.width <= 1200 and p.depth <= 1200]
    wide_pallets = [p for p in floor_pallets
                   if p.width > 1200 or p.depth > 1200]

    print(f"Узкопроходных: {len(narrow_pallets)}, Широких: {len(wide_pallets)}")

    # Разделить секции
    narrow_sections = [s for s in occupancy_s7 if s.narrowAisle]
    wide_sections = [s for s in occupancy_s7 if not s.narrowAisle]

    print(f"Секций узкопроходных: {len(narrow_sections)}, широких: {len(wide_sections)}")

    # ЗАДАЧА 1: Узкопроходные
    print("\n--- ЗАДАЧА 1: Узкопроходные ---")
    req_narrow = OptimizationRequest(
        optimizationId="PARALLEL-NARROW",
        mode="place",
        occupancy=narrow_sections,
        newPallets=narrow_pallets,
        settings=OptimizationSettingsSchema(
            allowReslot=False,
            maxOperations=5000,
            timeLimitSeconds=120,
            strictNarrowAislePlacement=True,
        ),
    )
    resp_narrow = run_optimization(req_narrow)

    print(f"Размещено: {resp_narrow.metrics.placedPallets}/{len(narrow_pallets)} "
          f"время={resp_narrow.executionTimeSeconds:.1f}s статус={resp_narrow.solverStatus}")

    # ЗАДАЧА 2: Широкие
    print("\n--- ЗАДАЧА 2: Широкие ---")
    req_wide = OptimizationRequest(
        optimizationId="PARALLEL-WIDE",
        mode="place",
        occupancy=wide_sections,
        newPallets=wide_pallets,
        settings=OptimizationSettingsSchema(
            allowReslot=False,
            maxOperations=5000,
            timeLimitSeconds=90,
            strictNarrowAislePlacement=True,
        ),
    )
    resp_wide = run_optimization(req_wide)

    print(f"Размещено: {resp_wide.metrics.placedPallets}/{len(wide_pallets)} "
          f"время={resp_wide.executionTimeSeconds:.1f}s статус={resp_wide.solverStatus}")

    total_placed = resp_narrow.metrics.placedPallets + resp_wide.metrics.placedPallets
    total_time = resp_narrow.executionTimeSeconds + resp_wide.executionTimeSeconds

    print(f"\n=== ИТОГО ===")
    print(f"Размещено: {total_placed}/{len(floor_pallets)} ({total_placed/len(floor_pallets)*100:.1f}%)")
    print(f"Время: {total_time:.1f}s")
    print(f"Эталон: {reference_count} (95.2%)")

    assert total_placed > 0
