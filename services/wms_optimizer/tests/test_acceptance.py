"""Приёмочные тесты WMS Pallet Optimizer (§21 ТЗ).

Запуск: pytest tests/test_acceptance.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from api.schemas import (
    OccupancySectionSchema, NewPalletSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from optimizer.global_optimizer import run_optimization


# ---------------------------------------------------------------------------
# Фабрики тестовых данных
# ---------------------------------------------------------------------------

def make_section_row(
    section_id: str, rack_id="R01", floor=1,
    width=3000, height=1800, depth=1200,
    max_weight=3000, gap_width=50, max_lift_weight=1000,
    max_pallets=3, unlimited_weight=False, max_width_pallet=0, max_depth_pallet=0,
    restricted=False, narrow_aisle=True,
    pallets=None,
) -> OccupancySectionSchema:
    """pallets: список до 3 словарей {id, width, height, depth, weight, quantity=1, blocked=0}
    (или None для пустого адреса)."""
    pallets = list(pallets or [])
    while len(pallets) < 3:
        pallets.append(None)

    kwargs = dict(
        section_id=section_id, section_code=section_id, rack_id=rack_id, rack_code=1, floor=floor,
        restricted=restricted, narrowAisle=narrow_aisle,
        typeSize_width=width, typeSize_height=height, typeSize_depth=depth, typeSize_weight=max_weight,
        typeSize_unlimitedWeight=unlimited_weight,
        gap_width=gap_width, max_lift_weight=max_lift_weight, max_pallets=max_pallets,
        max_widthPallet=max_width_pallet, max_depthPallet=max_depth_pallet,
    )
    for i, p in enumerate(pallets[:3], start=1):
        kwargs[f"address{i}"] = f"{section_id}-A{i}"
        if p:
            kwargs[f"pallet{i}_id"] = p["id"]
            kwargs[f"pallet{i}_code"] = p.get("code", p["id"])
            kwargs[f"pallet{i}_width"] = p["width"]
            kwargs[f"pallet{i}_height"] = p["height"]
            kwargs[f"pallet{i}_depth"] = p["depth"]
            kwargs[f"pallet{i}_weight"] = p["weight"]
            kwargs[f"quantity{i}"] = p.get("quantity", 1)
            kwargs[f"blocked{i}"] = p.get("blocked", 0)
    return OccupancySectionSchema(**kwargs)


def make_new_pallet(id: str, width=1200, height=1500, depth=1000, weight=700) -> NewPalletSchema:
    return NewPalletSchema(id=id, width=width, height=height, depth=depth, weight=weight)


def make_request(
    occupancy, new_pallets=None, settings=None, mode="place", opt_id="TEST-001",
) -> OptimizationRequest:
    return OptimizationRequest(
        optimizationId=opt_id,
        mode=mode,
        occupancy=occupancy,
        newPallets=new_pallets or [],
        settings=settings or OptimizationSettingsSchema(),
    )


# ---------------------------------------------------------------------------
# Тест 1: Простое размещение
# ---------------------------------------------------------------------------

def test_1_simple_placement():
    """Тест 1: свободная секция, 1 паллета → PUT, oldAddress=None."""
    occupancy = [make_section_row("SEC001")]
    req = make_request(occupancy, new_pallets=[make_new_pallet("P001")])
    resp = run_optimization(req)

    put_ops = [op for op in resp.operations if op.operation == "PUT"]
    assert len(put_ops) == 1, f"Ожидался 1 PUT, получено {len(put_ops)}"
    assert put_ops[0].pallet == "P001"
    assert put_ops[0].oldAddress is None
    assert resp.metrics.notPlacedPallets == 0


# ---------------------------------------------------------------------------
# Тест 2: Заполнение секции — 4-я паллета не влезает
# ---------------------------------------------------------------------------

def test_2_section_overflow():
    """Тест 2: 3 паллеты размещены, 4-я → notPlaced NO_SPACE."""
    # 3*900 + 4*50 = 2900 <= 3000 ✓, 4-я уже не влезает
    occupancy = [make_section_row("SEC001", width=3000, gap_width=50)]
    new_pallets = [make_new_pallet(f"P00{i}", width=900) for i in range(1, 5)]

    req = make_request(occupancy, new_pallets=new_pallets)
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 3, f"Ожидалось 3 размещённых, получено {resp.metrics.placedPallets}"
    assert len(resp.notPlaced) == 1
    assert resp.notPlaced[0].reason == "NO_SPACE"


# ---------------------------------------------------------------------------
# Тест 3: Ограничение веса подъёма
# ---------------------------------------------------------------------------

def test_3_lift_limit():
    """Тест 3: вес паллеты > maxLiftWeight всех секций → LIFT_LIMIT."""
    occupancy = [make_section_row("SEC001", max_lift_weight=500)]
    heavy_pallet = make_new_pallet("P001", weight=600)

    req = make_request(occupancy, new_pallets=[heavy_pallet])
    resp = run_optimization(req)

    assert len(resp.notPlaced) == 1
    assert resp.notPlaced[0].reason == "LIFT_LIMIT"


# ---------------------------------------------------------------------------
# Тест 4: Реслот запрещён
# ---------------------------------------------------------------------------

def test_4_no_reslot():
    """Тест 4: allowReslot=false → для существующей паллеты нет MOVE-операции."""
    occupancy = [
        make_section_row("SEC001", pallets=[{"id": "EP001", "width": 900, "height": 1500, "depth": 1000, "weight": 700}]),
        make_section_row("SEC002", rack_id="R02"),
    ]
    settings = OptimizationSettingsSchema(allowReslot=False)

    req = make_request(occupancy, settings=settings)
    resp = run_optimization(req)

    assert not any(op.pallet == "EP001" for op in resp.operations), (
        "Существующая паллета не должна двигаться при allowReslot=false"
    )


# ---------------------------------------------------------------------------
# Тест 5: Реслот позволяет разместить паллету, для которой иначе нет места
# ---------------------------------------------------------------------------

def test_5_reslot_improves_score():
    """Тест 5: score с allowReslot=true >= score с allowReslot=false (§20).

    Две узкие секции по 1000 ширины уже заняты паллетами по 400 —
    новая паллета 900 не влезает в отдельную секцию, только при консолидации.
    """
    occupancy = [
        make_section_row("SEC001", width=1000, gap_width=50,
                          pallets=[{"id": "EP001", "width": 400, "height": 1500, "depth": 1000, "weight": 700}]),
        make_section_row("SEC002", width=1000, gap_width=50,
                          pallets=[{"id": "EP002", "width": 400, "height": 1500, "depth": 1000, "weight": 700}]),
    ]
    new_pallets = [make_new_pallet("NP001", width=900)]

    req_no_reslot = make_request(
        occupancy, new_pallets=new_pallets,
        settings=OptimizationSettingsSchema(allowReslot=False, timeLimitSeconds=10),
    )
    req_reslot = make_request(
        occupancy, new_pallets=new_pallets,
        settings=OptimizationSettingsSchema(allowReslot=True, maxReslotPercent=100, timeLimitSeconds=10),
    )

    resp_no = run_optimization(req_no_reslot)
    resp_yes = run_optimization(req_reslot)

    assert resp_yes.score >= resp_no.score, (
        f"Score с реслотом ({resp_yes.score}) должен быть >= без реслота ({resp_no.score})"
    )
    assert resp_no.metrics.placedPallets == 0, "Без реслота новой паллете 900 негде поместиться"
    assert resp_yes.metrics.placedPallets == 1, "С реслотом новая паллета должна быть размещена"


# ---------------------------------------------------------------------------
# Тест 6: PotentialLoss не отрицателен
# ---------------------------------------------------------------------------

def test_6_potential_loss_non_negative():
    """Тест 6: после размещения metrics.potentialLoss >= 0."""
    occupancy = [make_section_row("SEC001")]
    req = make_request(occupancy, new_pallets=[make_new_pallet("P001")])
    resp = run_optimization(req)

    assert resp.metrics.potentialLoss >= 0


# ---------------------------------------------------------------------------
# Тест 7: Некорректные входные данные — дублирующийся id паллеты
# ---------------------------------------------------------------------------

def test_7_invalid_data_duplicate_pallet():
    """Тест 7: один и тот же id паллеты в двух секциях → ValidationError."""
    from validation.validator import ValidationError, validate_request

    occupancy = [
        make_section_row("SEC001", pallets=[{"id": "DUP001", "width": 900, "height": 1500, "depth": 1000, "weight": 700}]),
        make_section_row("SEC002", rack_id="R02", pallets=[{"id": "DUP001", "width": 900, "height": 1500, "depth": 1000, "weight": 700}]),
    ]
    req = make_request(occupancy)

    with pytest.raises(ValidationError) as exc_info:
        validate_request(req)

    assert exc_info.value.reason == "INVALID_DATA"


# ---------------------------------------------------------------------------
# Тест 8: Разделение solverStatus и placementStatus
# ---------------------------------------------------------------------------

def test_8_status_independence():
    """Тест 8: solverStatus и placementStatus — независимые поля."""
    occupancy = [make_section_row("SEC001")]
    req = make_request(
        occupancy,
        new_pallets=[make_new_pallet("P001")],
        settings=OptimizationSettingsSchema(timeLimitSeconds=1),
    )
    resp = run_optimization(req)

    assert resp.solverStatus in ("OPTIMAL", "FEASIBLE", "TIME_LIMIT", "INFEASIBLE")
    assert resp.placementStatus in ("FULL", "PARTIAL", "NONE")


# ---------------------------------------------------------------------------
# Тест 9: Лимит maxOperations (§6 ТЗ) — считает PUT+MOVE
# ---------------------------------------------------------------------------

def test_9_max_operations_limit():
    """maxOperations=2 → не более 2 операций PUT/MOVE в плане при 5 доступных местах."""
    occupancy = [
        make_section_row(f"SEC{i:03d}", rack_id=f"R{i:02d}") for i in range(1, 3)
    ]
    new_pallets = [make_new_pallet(f"NP{i:03d}") for i in range(1, 6)]
    settings = OptimizationSettingsSchema(maxOperations=2)

    req = make_request(occupancy, new_pallets=new_pallets, settings=settings)
    resp = run_optimization(req)

    put_move_ops = [op for op in resp.operations if op.operation in ("PUT", "MOVE")]
    assert len(put_move_ops) <= 2, f"Ожидалось не более 2 PUT/MOVE, получено {len(put_move_ops)}"


# ---------------------------------------------------------------------------
# Тест 10: mode=compact не принимает newPallets
# ---------------------------------------------------------------------------

def test_10_compact_mode_rejects_new_pallets():
    """mode='compact' с непустым newPallets → ошибка валидации Pydantic."""
    occupancy = [make_section_row("SEC001")]
    with pytest.raises(ValueError):
        make_request(occupancy, new_pallets=[make_new_pallet("P001")], mode="compact")


# ---------------------------------------------------------------------------
# Тест 11: Заблокированная паллета не двигается и не участвует в реслоте
# ---------------------------------------------------------------------------

def test_11_blocked_pallet_never_moves():
    """Тест 11: паллета с blocked>0 фиксирована на месте даже при allowReslot=true."""
    occupancy = [
        make_section_row("SEC001", width=1000, gap_width=50,
                          pallets=[{"id": "BP001", "width": 400, "height": 1500, "depth": 1000, "weight": 700, "quantity": 0, "blocked": 1}]),
        make_section_row("SEC002", rack_id="R02", width=1000, gap_width=50),
    ]
    settings = OptimizationSettingsSchema(allowReslot=True, maxReslotPercent=100)

    req = make_request(occupancy, settings=settings)
    resp = run_optimization(req)

    assert not any(op.pallet == "BP001" for op in resp.operations), (
        "Заблокированная паллета не должна перемещаться"
    )


# ---------------------------------------------------------------------------
# Тест 12: Restricted-секция полностью исключена
# ---------------------------------------------------------------------------

def test_12_restricted_section_excluded():
    """Тест 12: секция с restricted=true никогда не получает новую паллету."""
    occupancy = [
        make_section_row("SEC001", restricted=True),
    ]
    req = make_request(occupancy, new_pallets=[make_new_pallet("P001")])
    resp = run_optimization(req)

    assert resp.metrics.placedPallets == 0
    assert len(resp.notPlaced) == 1
