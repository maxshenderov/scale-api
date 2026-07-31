"""Валидация ответа оптимизатора на произвольных данных.

Использование:
  1. Сохрани occupancy JSON в tests/example/ProductionOccupancy.json
  2. Сохрани floor pallets JSON в tests/example/ProductionFloor.json
     (формат: {"floorPallets": [{"width":..., "height":..., "depth":..., "weight":...}, ...]})
  3. Сохрани response JSON в tests/example/ProductionResponse.json
     (формат: {"operations": [{"pallet":..., "operation":..., "newAddress":..., ...}, ...]})
  4. Запусти: python tests/test_validate_production.py

Или для проверки только occupancy+floor (response считается оптимизатором):
  python tests/test_validate_production.py --optimize
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationResponse, OptimizationSettingsSchema,
)
from optimizer.global_optimizer import run_optimization
from tests.test_validate_operations import _validate_operations, _print_errors

EXAMPLE_DIR = os.path.join(os.path.dirname(__file__), "example")


def load_production_data():
    """Загружает production occupancy, floor pallets и (опционально) response."""
    occ_path = os.path.join(EXAMPLE_DIR, "ProductionOccupancy.json")
    floor_path = os.path.join(EXAMPLE_DIR, "ProductionFloor.json")
    resp_path = os.path.join(EXAMPLE_DIR, "ProductionResponse.json")

    if not os.path.exists(occ_path):
        print(f"ERROR: {occ_path} не найден.")
        print("Положи occupancy JSON (из тела POST /api/optimize) в этот файл.")
        sys.exit(1)

    if not os.path.exists(floor_path):
        print(f"ERROR: {floor_path} не найден.")
        print("Положи floor pallets JSON в этот файл.")
        print('Формат: {"floorPallets": [{"width":..., "height":..., "depth":..., "weight":...}, ...]}')
        sys.exit(1)

    with open(occ_path, encoding="utf-8") as f:
        raw = json.load(f)
    occupancy = [OccupancySectionSchema(**row) for row in raw["sections"]]

    with open(floor_path, encoding="utf-8") as f:
        raw = json.load(f)
    floor_pallets = [
        NewPalletSchema(
            id=f"FLOOR-{i:04d}",
            width=p["width"], height=p["height"], depth=p["depth"], weight=p["weight"],
        )
        for i, p in enumerate(raw["floorPallets"])
    ]

    response = None
    if os.path.exists(resp_path):
        with open(resp_path, encoding="utf-8") as f:
            raw = json.load(f)
        from api.schemas import OperationSchema
        response = type('Response', (), {
            'operations': [OperationSchema(**op) for op in raw["operations"]],
            'metrics': type('Metrics', (), raw.get("metrics", {})),
        })()
        print(f"Загружен готовый response: {len(response.operations)} операций")

    return occupancy, floor_pallets, response


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Валидация production-данных")
    parser.add_argument("--optimize", action="store_true",
                        help="Запустить оптимизатор (вместо загрузки готового response)")
    parser.add_argument("--solver", default="numpy", choices=["numpy", "lp", "cp_sat"],
                        help="Тип солвера (по умолчанию: numpy)")
    parser.add_argument("--two-stage", action="store_true", default=True,
                        help="Двухэтапный режим (по умолчанию)")
    parser.add_argument("--no-two-stage", action="store_true",
                        help="Отключить двухэтапный режим")
    parser.add_argument("--time-limit", type=int, default=300,
                        help="Лимит времени в секундах")
    args = parser.parse_args()

    occupancy, floor_pallets, response = load_production_data()

    if args.optimize or response is None:
        print(f"Запуск оптимизатора ({args.solver})...")
        settings = OptimizationSettingsSchema(
            allowReslot=False,
            maxOperations=5000,
            timeLimitSeconds=args.time_limit,
            strictNarrowAislePlacement=True,
            twoStageReslot=args.two_stage and not args.no_two_stage,
            twoStageReslotMaxReslotPercent=40.0,
            twoStageReslotTimeLimitSeconds=120,
            solverType=args.solver,
        )
        req = OptimizationRequest(
            optimizationId=f"PROD-VALIDATE-{args.solver}",
            mode="place",
            occupancy=occupancy,
            newPallets=floor_pallets,
            settings=settings,
        )
        resp = run_optimization(req)
        print(f"Оптимизатор: placed={resp.metrics.placedPallets} "
              f"moved={resp.metrics.movedPallets} "
              f"operations={len(resp.operations)} "
              f"time={resp.executionTimeSeconds:.1f}s")
    else:
        resp = response
        print(f"Валидация готового response: {len(resp.operations)} операций")

    errors, duplicates, error_reasons, virtual_state, section_by_id, address_by_id, pallet_dimensions = \
        _validate_operations(resp, occupancy, floor_pallets)

    _print_errors(errors, error_reasons, section_by_id, address_by_id, pallet_dimensions, virtual_state)

    total_ops = len(resp.operations) if hasattr(resp.operations, '__len__') else 0
    error_count = len(errors)

    if duplicates:
        print(f"\nДУБЛИКАТЫ АДРЕСОВ ({len(duplicates)} шт):")
        for addr, pallets in list(duplicates.items())[:10]:
            print(f"  {addr}: {pallets}")

    if error_count == 0 and len(duplicates) == 0:
        print(f"\nOK: все {total_ops} операций валидны для 1С")
    else:
        print(f"\nПРОВАЛ: {error_count} ошибок, {len(duplicates)} дубликатов из {total_ops} операций")
        sys.exit(1)


if __name__ == "__main__":
    main()
