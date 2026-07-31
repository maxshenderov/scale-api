"""
Локальная оптимизация заполнения одной секции.

Задача: дана секция и доступные типоразмеры паллет с остатками.
Найти: оптимальный набор паллет для размещения в эту секцию.

Алгоритм: жадный с look-ahead — проверяет "если взять этот паллет,
можно ли заполнить остаток лучше несколькими мелкими".
"""

from typing import List, Dict, Tuple, Optional


def optimize_section_fill(
    section: Dict,
    available_types: List[Dict],
    gap_width: float = 50.0,
) -> List[Dict]:
    """
    Оптимальное заполнение одной секции.

    Args:
        section: {
            "width": 2700,
            "height": 2400,
            "depth": 1200,
            "max_pallets": 3,
            "max_weight": 3000,
            "narrow_aisle": False,
            "max_width_pallet": None
        }
        available_types: [
            {"width": 1200, "height": 2200, "depth": 1000, "weight": 800, "count": 5},
            ...
        ]
        gap_width: зазор между паллетами (мм)

    Returns:
        [{"typeIndex": 0, "count": 2}, {"typeIndex": 1, "count": 1}]
    """
    max_width = section["width"]
    max_height = section["height"]
    max_depth = section["depth"]
    max_pallets = section.get("max_pallets", 3)
    max_weight = section.get("max_weight") or float('inf')
    narrow_aisle = section.get("narrow_aisle", False)
    max_width_pallet = section.get("max_width_pallet")

    # Фильтр: только физически допустимые типы
    feasible = []
    for i, t in enumerate(available_types):
        if t["count"] <= 0:
            continue
        if t["height"] > max_height or t["depth"] > max_depth:
            continue
        if t["width"] + gap_width > max_width:
            continue
        # Узкопроходность
        if narrow_aisle and max_width_pallet and t["width"] > max_width_pallet:
            continue

        feasible.append((i, t))

    if not feasible:
        return []

    # Жадный с look-ahead
    result = _greedy_with_lookahead(
        feasible, max_width, max_pallets, max_weight, gap_width
    )

    return result


def _greedy_with_lookahead(
    feasible: List[Tuple[int, Dict]],
    max_width: float,
    max_pallets: int,
    max_weight: float,
    gap_width: float,
) -> List[Dict]:
    """Жадный алгоритм с проверкой остатка."""

    result = []
    used_width = gap_width  # Начальный зазор
    used_pallets = 0
    used_weight = 0.0
    available = {i: t["count"] for i, t in feasible}

    while used_pallets < max_pallets:
        # Остаток ширины
        remaining_width = max_width - used_width

        # Кандидаты: что влезает в остаток (с учётом зазора после паллета)
        candidates = [
            (i, t) for i, t in feasible
            if available[i] > 0
            and t["width"] + gap_width <= remaining_width
            and used_weight + t["weight"] <= max_weight
        ]

        if not candidates:
            break

        # Правило выбора: всегда look-ahead (проверяем можно ли заполнить остаток лучше)
        best_idx, best_type = _choose_best_for_remainder(
            candidates,
            remaining_width,
            max_pallets - used_pallets,
            max_weight - used_weight,
            gap_width,
            available,
            max_width,  # Для расчёта утилизации
        )

        # Добавить выбранный паллет
        result.append({"typeIndex": best_idx, "type": best_type})
        used_width += best_type["width"] + gap_width
        used_pallets += 1
        used_weight += best_type["weight"]
        available[best_idx] -= 1

    # Сгруппировать одинаковые типы
    grouped = {}
    for r in result:
        idx = r["typeIndex"]
        if idx not in grouped:
            grouped[idx] = {"typeIndex": idx, "count": 0}
        grouped[idx]["count"] += 1

    return list(grouped.values())


def _choose_best_for_remainder(
    candidates: List[Tuple[int, Dict]],
    remaining_width: float,
    remaining_slots: int,
    remaining_weight: float,
    gap_width: float,
    available: Dict[int, int],
    max_width: float,
) -> Tuple[int, Dict]:
    """
    Look-ahead: если взять этот паллет, сколько ещё влезет?

    Пример: остаток 2700мм, 3 слота.
    - Вариант А: 1×1200 → остаток 1500 → ещё 1×1200 → остаток 300 → ничего → 2 паллеты, 2400мм
    - Вариант Б: 1×900 → остаток 1800 → ещё 1×900 → остаток 900 → ещё 1×900 → 3 паллеты, 2700мм

    Выбираем Б.
    """
    scores = []

    for i, t in candidates:
        # Симуляция: жадно заполнить остаток начиная с этого паллета
        sim_avail = available.copy()
        sim_selected = []  # Храним выбранные паллеты
        sim_slots = 0
        sim_weight = 0.0
        sim_remaining = remaining_width

        # Жадно добавлять паллеты пока влезают
        while sim_slots < remaining_slots:
            # Кандидаты для добавления
            sim_candidates = [
                (j, t2) for j, t2 in candidates
                if sim_avail.get(j, 0) > 0
                and t2["width"] + gap_width <= sim_remaining
                and sim_weight + t2["weight"] <= remaining_weight
            ]

            if not sim_candidates:
                break

            # На первой итерации берём исходный паллет i
            if sim_slots == 0:
                next_j, next_t = (i, t)
            else:
                # На следующих — пытаемся взять тот же тип (гомогенное заполнение)
                same_type_cand = [(j, t2) for j, t2 in sim_candidates if j == i]
                if same_type_cand:
                    next_j, next_t = same_type_cand[0]
                else:
                    # Если тот же тип не влезает — берём самый широкий из оставшихся
                    next_j, next_t = max(sim_candidates, key=lambda x: x[1]["width"])

            sim_selected.append(next_t)
            sim_slots += 1
            sim_weight += next_t["weight"]
            sim_remaining -= next_t["width"] + gap_width
            sim_avail[next_j] = sim_avail.get(next_j, 0) - 1

        # Скор: количество паллет × 1000 + использование ширины × 100
        # utilization = (чистая ширина паллет) / (доступная ширина секции)
        net_pallet_width = sum(p["width"] for p in sim_selected)
        utilization = net_pallet_width / max_width if max_width > 0 else 0
        score = sim_slots * 1000 + utilization * 100
        scores.append((score, (i, t)))

    # Вернуть лучший вариант
    return max(scores, key=lambda x: x[0])[1]