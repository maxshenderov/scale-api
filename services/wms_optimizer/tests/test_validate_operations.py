"""Валидация операций NumPy-солвера: симуляция проверок 1С.

Проверяет каждую операцию (PUT/MOVE) из ответа солвера на соответствие
правилам размещения, которые 1С применяет через ОшибкиРазмещенияПаллетаВАдрес().

Если этот тест падает — план размещения содержит ошибки, которые 1С отклонит.

Версия 2: полное покрытие всех 17 проверок из ОшибкиРазмещенияВАдрес() (ManagerModule.bsl:2696).
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.schemas import (
    NewPalletSchema, OccupancySectionSchema,
    OptimizationRequest, OptimizationSettingsSchema,
)
from models.occupancy_builder import build_warehouse_state
from optimizer.global_optimizer import run_optimization

EXAMPLE_DIR = os.path.join(os.path.dirname(__file__), "example")

SETTINGS = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=300,
    strictNarrowAislePlacement=True,
    twoStageReslot=True,
    twoStageReslotMaxReslotPercent=40.0,
    twoStageReslotTimeLimitSeconds=120,
    solverType="numpy",
)

SETTINGS_LP = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=300,
    strictNarrowAislePlacement=True,
    twoStageReslot=True,
    twoStageReslotMaxReslotPercent=40.0,
    twoStageReslotTimeLimitSeconds=120,
    solverType="lp",
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


def _validate_operations(resp, occupancy, floor_pallets):
    """Validate all operations against 1C rules. Returns (errors, duplicates)."""
    sections, addresses, existing_pallets = build_warehouse_state(occupancy)

    section_by_id = {s.id: s for s in sections}
    address_by_id = {a.id: a for a in addresses}

    # Карта: pallet_id → {width, height, depth, weight}
    pallet_dimensions = {}

    for ep in existing_pallets:
        ts = ep.type_size
        pallet_dimensions[ep.id] = {
            "width": ts.width, "height": ts.height,
            "depth": ts.depth, "weight": ts.weight,
        }

    for fp in floor_pallets:
        pallet_dimensions[fp.id] = {
            "width": fp.width, "height": fp.height,
            "depth": fp.depth, "weight": fp.weight,
        }

    # Виртуальное состояние: address_id → pallet_id
    virtual_state = {}
    for addr in addresses:
        if addr.pallet_id:
            virtual_state[addr.id] = addr.pallet_id

    errors = []
    error_reasons = Counter()

    for op in resp.operations:
        pallet_id = op.pallet
        target_addr_id = op.newAddress
        operation_type = op.operation

        # Для MOVE: сначала освобождаем старый адрес
        if operation_type == "MOVE" and op.oldAddress:
            old_occupant = virtual_state.get(op.oldAddress)
            if old_occupant == pallet_id:
                del virtual_state[op.oldAddress]

        # --- 1. Адрес существует ---
        target_addr = address_by_id.get(target_addr_id)
        if target_addr is None:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "error": "ADDRESS_NOT_FOUND",
            })
            error_reasons["ADDRESS_NOT_FOUND"] += 1
            continue

        section = section_by_id.get(target_addr.section_id)
        if section is None:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "error": "SECTION_NOT_FOUND",
            })
            error_reasons["SECTION_NOT_FOUND"] += 1
            continue

        # --- 2. Адрес не занят (1С проверка 1) ---
        occupying = virtual_state.get(target_addr_id)
        if occupying is not None and occupying != pallet_id:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "ADDRESS_OCCUPIED",
                "occupant": occupying,
            })
            error_reasons["ADDRESS_OCCUPIED"] += 1
            continue

        # --- 3. Габариты паллета известны ---
        pdim = pallet_dimensions.get(pallet_id)
        if pdim is None:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "error": "PALLET_DIMS_UNKNOWN",
            })
            error_reasons["PALLET_DIMS_UNKNOWN"] += 1
            continue

        p_w = pdim["width"]
        p_h = pdim["height"]
        p_d = pdim["depth"]
        p_wt = pdim["weight"]

        # --- 4. Высота (1С проверка 11) ---
        if p_h > section.height:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "HEIGHT_LIMIT",
                "pallet_height": p_h, "section_height": section.height,
            })
            error_reasons["HEIGHT_LIMIT"] += 1
            continue

        # --- 5. Глубина (1С проверка 12) ---
        if p_d > section.depth:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "DEPTH_LIMIT",
            })
            error_reasons["DEPTH_LIMIT"] += 1
            continue

        # --- 6. Вес подъёма (1С проверка 15) ---
        if not math.isinf(section.max_lift_weight) and p_wt > section.max_lift_weight:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "LIFT_WEIGHT_LIMIT",
            })
            error_reasons["LIFT_WEIGHT_LIMIT"] += 1
            continue

        # --- 7. Узкопроходность ---
        pallet_is_narrow = p_w <= 1200 and p_d <= 1200
        if section.narrow_aisle and not pallet_is_narrow:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "NARROW_AISLE_MISMATCH",
            })
            error_reasons["NARROW_AISLE_MISMATCH"] += 1
            continue

        # ═══════════════════════════════════════════════════════════════════
        # ГЕОМЕТРИЧЕСКИЕ КОНФЛИКТЫ АДРЕСОВ (1С проверки 2-9)
        # Правила из ОшибкиРазмещенияВАдрес():
        #   - паллет > 2/3 секции → только центр (адрес 2)
        #   - паллет > 1/3 секции → до 2 паллет в края (1 и 3)
        #   - паллет <= 1/3 секции → до 3 паллет во все адреса
        #   - широкий (>W/3) в центре блокирует оба края
        #   - широкий (>W/3) на краю блокирует центр
        # ═══════════════════════════════════════════════════════════════════

        sec_w = section.width
        target_pos = target_addr.position

        # Карта занятых позиций в секции (position → pallet_dims)
        occupied_positions = {}
        for aid, pid in virtual_state.items():
            addr = address_by_id.get(aid)
            if addr and addr.section_id == section.id and pid is not None:
                occupied_positions[addr.position] = pallet_dimensions.get(pid, {})

        geo_errors = []

        # Проверка 2 (1С): addr1 занят, если центр занят широким (>W/3)
        if target_pos == 1 and 2 in occupied_positions:
            p2_w = occupied_positions[2].get("width", 0)
            if p2_w > sec_w / 3:
                geo_errors.append("ADDR1_BLOCKED_BY_WIDE_CENTER")

        # Проверка 3 (1С): addr3 занят, если центр занят широким (>W/3)
        if target_pos == 3 and 2 in occupied_positions:
            p2_w = occupied_positions[2].get("width", 0)
            if p2_w > sec_w / 3:
                geo_errors.append("ADDR3_BLOCKED_BY_WIDE_CENTER")

        # Проверка 4 (1С): addr2 занят, если addr1 занят широким (>W/3)
        if target_pos == 2 and 1 in occupied_positions:
            p1_w = occupied_positions[1].get("width", 0)
            if p1_w > sec_w / 3:
                geo_errors.append("ADDR2_BLOCKED_BY_WIDE_ADDR1")

        # Проверка 5 (1С): addr2 занят, если addr3 занят широким (>W/3)
        if target_pos == 2 and 3 in occupied_positions:
            p3_w = occupied_positions[3].get("width", 0)
            if p3_w > sec_w / 3:
                geo_errors.append("ADDR2_BLOCKED_BY_WIDE_ADDR3")

        # Проверка 6 (1С): новый паллет > W/3 в центр при занятом addr1
        if target_pos == 2 and 1 in occupied_positions and p_w > sec_w / 3:
            geo_errors.append("WIDE_PALLET_CENTER_WITH_ADDR1")

        # Проверка 7 (1С): новый паллет > W/3 в центр при занятом addr3
        if target_pos == 2 and 3 in occupied_positions and p_w > sec_w / 3:
            geo_errors.append("WIDE_PALLET_CENTER_WITH_ADDR3")

        # Проверка 8 (1С): новый паллет > 2W/3 в крайний адрес 1
        if target_pos == 1 and p_w > sec_w * 2 / 3:
            geo_errors.append("WIDE_PALLET_ON_EDGE_ADDR1")

        # Проверка 9 (1С): новый паллет > 2W/3 в крайний адрес 3
        if target_pos == 3 and p_w > sec_w * 2 / 3:
            geo_errors.append("WIDE_PALLET_ON_EDGE_ADDR3")

        if geo_errors:
            for ge in geo_errors:
                error_reasons[ge] += 1
                errors.append({
                    "pallet": pallet_id, "operation": operation_type,
                    "address": target_addr_id, "section": section.id,
                    "error": ge,
                    "pallet_width": p_w, "section_width": sec_w,
                })
            continue

        # ═══════════════════════════════════════════════════════════════════
        # ШИРИНА СЕКЦИИ (с зазорами)
        # ═══════════════════════════════════════════════════════════════════

        pals_in_section_widths = []
        for aid, pid in virtual_state.items():
            addr = address_by_id.get(aid)
            if addr and addr.section_id == section.id and pid is not None:
                pd = pallet_dimensions.get(pid, {})
                pw = pd.get("width", 0)
                if pw > 0:
                    pals_in_section_widths.append(pw)

        pals_in_section_widths.append(p_w)
        n_pallets = len(pals_in_section_widths)
        total_width = sum(pals_in_section_widths)
        required_width = total_width + (n_pallets + 1) * section.gap_width

        if required_width > section.width:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "WIDTH_OVERFLOW",
                "required": round(required_width, 1),
                "section_width": section.width,
                "n_pallets": n_pallets,
            })
            error_reasons["WIDTH_OVERFLOW"] += 1
            continue

        # ═══════════════════════════════════════════════════════════════════
        # МОНТАЖНЫЙ ЗАЗОР 150/200 мм (1С проверка 13a)
        # Если в секции уже есть паллеты — нужен ДОПОЛНИТЕЛЬНЫЙ зазор
        # сверх базового gap_width: 150 мм для 1 существующего, 200 мм для 2.
        # Формула 1С: ОстатокШиринаСекции - Ширина - ТребуемыйЗазор >= 0,
        # где ОстатокШиринаСекции = section.width - occupied_widths
        # (БЕЗ учёта gaps — 1С считает от полной ширины секции, не от remaining)
        # ═══════════════════════════════════════════════════════════════════

        occupied_width = sum(pals_in_section_widths) - p_w  # без нового
        existing_count = n_pallets - 1  # без нового

        if existing_count >= 1:
            extra_gap = 150 if existing_count == 1 else 200
            if section.width - occupied_width - p_w - extra_gap < 0:
                errors.append({
                    "pallet": pallet_id, "operation": operation_type,
                    "address": target_addr_id, "section": section.id,
                    "error": "MOUNTING_GAP",
                    "occupied_width": occupied_width,
                    "pallet_width": p_w,
                    "extra_gap": extra_gap,
                    "section_width": section.width,
                    "required": occupied_width + p_w + extra_gap,
                    "existing_count": existing_count,
                })
                error_reasons["MOUNTING_GAP"] += 1
                continue

        # --- 9. Общий вес секции (1С проверка 13) ---
        if not math.isinf(section.max_weight):
            weights_in_section = []
            for aid, pid in virtual_state.items():
                addr = address_by_id.get(aid)
                if addr and addr.section_id == section.id and pid is not None:
                    pd = pallet_dimensions.get(pid, {})
                    pw = pd.get("weight", 0)
                    if pw > 0:
                        weights_in_section.append(pw)
            total_weight = sum(weights_in_section) + p_wt
            if total_weight > section.max_weight:
                errors.append({
                    "pallet": pallet_id, "operation": operation_type,
                    "address": target_addr_id, "section": section.id,
                    "error": "WEIGHT_OVERFLOW",
                })
                error_reasons["WEIGHT_OVERFLOW"] += 1
                continue

        # --- 10. Макс. количество паллет в секции ---
        current_count = sum(
            1 for aid, pid in virtual_state.items()
            if address_by_id.get(aid) and address_by_id[aid].section_id == section.id
        )
        if current_count >= section.max_pallets:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "MAX_PALLETS_EXCEEDED",
                "current": current_count, "max": section.max_pallets,
            })
            error_reasons["MAX_PALLETS_EXCEEDED"] += 1
            continue

        # --- 11. eff_max_width (1С проверка в ПодобратьЯчейку) ---
        if p_w > section.eff_max_width:
            errors.append({
                "pallet": pallet_id, "operation": operation_type,
                "address": target_addr_id, "section": section.id,
                "error": "EFF_MAX_WIDTH",
            })
            error_reasons["EFF_MAX_WIDTH"] += 1
            continue

        # --- Все проверки пройдены — фиксируем ---
        virtual_state[target_addr_id] = pallet_id

    # Дубликаты адресов в операциях
    addr_assignments = defaultdict(list)
    for op in resp.operations:
        addr_assignments[op.newAddress].append(op.pallet)
    duplicates = {a: ps for a, ps in addr_assignments.items() if len(ps) > 1}

    return errors, duplicates, error_reasons, virtual_state, section_by_id, address_by_id, pallet_dimensions


def _print_errors(errors, error_reasons, section_by_id, address_by_id, pallet_dimensions, virtual_state):
    """Print detailed error information."""
    total_ops = len(virtual_state)  # approximate
    error_count = len(errors)

    print(f"\n=== Результаты валидации ===")
    print(f"Ошибок: {error_count}")
    print(f"Причины ошибок: {dict(error_reasons)}")

    if errors:
        print(f"\nПервые 50 ошибок:")
        for err in errors[:50]:
            extra = ""
            for k in ("occupant", "required", "section_width", "n_pallets",
                       "current", "max", "pallet_height", "section_height",
                       "pallet_width", "occupied_width", "extra_gap", "existing_count"):
                if k in err:
                    extra += f" {k}={err[k]}"
            print(f"  [{err['error']}] pallet={err['pallet']} addr={err['address']}"
                  f" section={err.get('section', '?')}{extra}")

        # Анализ геометрических ошибок
        geo_errors = [e for e in errors if e["error"].startswith("ADDR") or e["error"].startswith("WIDE_PALLET")]
        if geo_errors:
            print(f"\n=== Геометрические конфликты адресов ({len(geo_errors)} шт) ===")
            by_type = Counter(e["error"] for e in geo_errors)
            for err_type, count in by_type.most_common():
                print(f"  {err_type}: {count}")
                # Показать первый пример
                ex = next(e for e in geo_errors if e["error"] == err_type)
                addr = address_by_id.get(ex["address"])
                if addr:
                    print(f"    Пример: addr={ex['address']} pos={addr.position}"
                          f" pallet_w={ex.get('pallet_width')} section_w={ex.get('section_width')}")

        # Анализ MOUNTING_GAP
        gap_errors = [e for e in errors if e["error"] == "MOUNTING_GAP"]
        if gap_errors:
            print(f"\n=== Ошибки монтажного зазора ({len(gap_errors)} шт) ===")
            by_section = defaultdict(list)
            for e in gap_errors:
                by_section[e.get("section", "?")].append(e)
            for sec_id, sec_errors in list(by_section.items())[:5]:
                sec = section_by_id.get(sec_id)
                if sec:
                    print(f"  Секция {sec_id} (rack={sec.rack_code} W={sec.width} gap={sec.gap_width}):")
                    for e in sec_errors[:3]:
                        print(f"    pallet_w={e['pallet_width']} occupied={e['occupied_width']}"
                              f" extra_gap={e['extra_gap']} required={e['required']} section_w={e['section_width']}"
                              f" existing={e['existing_count']}")

        # Анализ WIDTH_OVERFLOW
        width_errors = [e for e in errors if e["error"] == "WIDTH_OVERFLOW"]
        if width_errors:
            print(f"\n=== Детальный анализ WIDTH_OVERFLOW ({len(width_errors)} шт) ===")
            by_section = defaultdict(list)
            for e in width_errors:
                by_section[e.get("section", "?")].append(e)
            top_sections = sorted(by_section.items(), key=lambda x: -len(x[1]))[:5]
            for sec_id, sec_errors in top_sections:
                sec = section_by_id.get(sec_id)
                if sec:
                    print(f"\n  Секция {sec_id} (rack={sec.rack_code} floor={sec.floor}"
                          f" W={sec.width} H={sec.height} gap={sec.gap_width}"
                          f" narrow={sec.narrow_aisle} max_pallets={sec.max_pallets}):")
                    existing_in_sec = []
                    for aid, pid in virtual_state.items():
                        addr = address_by_id.get(aid)
                        if addr and addr.section_id == sec_id and pid is not None:
                            pd = pallet_dimensions.get(pid, {})
                            existing_in_sec.append((pid, pd.get("width", 0), pd.get("weight", 0)))
                    print(f"    Размещено до ошибок: {len(existing_in_sec)} паллет")
                    for pid, pw, pwt in existing_in_sec[-5:]:
                        print(f"      {pid}: W={pw} WT={pwt}")
                    print(f"    Ошибок размещения: {len(sec_errors)}")
                    for e in sec_errors[:3]:
                        pd = pallet_dimensions.get(e["pallet"], {})
                        print(f"      [{e['pallet']}] W={pd.get('width')} H={pd.get('height')}"
                              f" WT={pd.get('weight')} n_pallets={e.get('n_pallets')}"
                              f" required={e.get('required')} section_w={e.get('section_width')}")


@pytest.mark.slow
@pytest.mark.parametrize("settings", [SETTINGS, SETTINGS_LP], ids=["numpy", "lp"])
def test_validate_all_operations(settings):
    """Каждая операция солвера должна пройти симуляцию проверок 1С."""
    occupancy_s7 = _load_occupancy("OccupancyS7.json")
    floor_pallets = _load_floor_pallets()

    req = OptimizationRequest(
        optimizationId=f"S7-VALIDATE-{settings.solverType}",
        mode="place",
        occupancy=occupancy_s7,
        newPallets=floor_pallets,
        settings=settings,
    )
    resp = run_optimization(req)

    print(f"\n=== Валидация операций {settings.solverType.upper()} ===")
    print(f"solverStatus={resp.solverStatus} placementStatus={resp.placementStatus}")
    print(f"executionTimeSeconds={resp.executionTimeSeconds}")
    print(f"placedPallets={resp.metrics.placedPallets}")
    print(f"movedPallets={resp.metrics.movedPallets}")
    print(f"Всего операций: {len(resp.operations)}")

    errors, duplicates, error_reasons, virtual_state, section_by_id, address_by_id, pallet_dimensions = \
        _validate_operations(resp, occupancy_s7, floor_pallets)

    _print_errors(errors, error_reasons, section_by_id, address_by_id, pallet_dimensions, virtual_state)

    total_ops = len(resp.operations)
    error_count = len(errors)

    if duplicates:
        print(f"\nДУБЛИКАТЫ АДРЕСОВ В ПЛАНЕ ({len(duplicates)} шт):")
        for addr, pallets in list(duplicates.items())[:10]:
            print(f"  {addr}: {pallets}")

    assert len(duplicates) == 0, (
        f"План содержит {len(duplicates)} адресов с дублирующимися паллетами!"
    )
    assert error_count == 0, (
        f"{error_count} из {total_ops} операций не прошли валидацию! "
        f"Причины: {dict(error_reasons)}"
    )

    print(f"\nOK: все {total_ops} операций валидны для 1С")


if __name__ == "__main__":
    test_validate_all_operations()
