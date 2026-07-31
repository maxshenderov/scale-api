"""
Тест разных стратегий сортировки паллет для локальной оптимизации.

Сравниваем:
1. Текущая: высокие → тяжёлые
2. Популярные первыми (по количеству типоразмера)
3. Редкие первыми (мало секций подходит)
4. Гибридная: популярные + редкие по секциям
"""
import json
from collections import defaultdict, Counter
from optimizer.section_packer import optimize_section_fill


def load_s7_data():
    """Загрузить данные S7."""
    with open("tests/example/OccupancyS7.json", "r", encoding="utf-8") as f:
        occupancy = json.load(f)
    with open("tests/example/FloorS7.json", "r", encoding="utf-8") as f:
        floor = json.load(f)
    return occupancy, floor


def parse_sections(occupancy):
    """Парсинг секций."""
    sections = []
    for sec in occupancy.get("sections", []):
        max_weight = sec.get("typeSize_weight")
        if sec.get("typeSize_unlimitedWeight"):
            max_weight = 999999
        sections.append({
            "id": sec.get("section_code", ""),
            "width": sec.get("typeSize_width", 0),
            "height": sec.get("typeSize_height", 0),
            "depth": sec.get("typeSize_depth", 0),
            "max_pallets": sec.get("max_pallets", 3),
            "max_weight": max_weight,
            "narrow_aisle": sec.get("narrowAisle", False),
            "max_width_pallet": sec.get("max_widthPallet", 1200),
            "used_width": 0,
            "used_weight": 0,
            "used_pallets": 0,
            "placed_pallet_ids": [],
        })
    return sections


def parse_pallets(floor):
    """Парсинг паллет."""
    pallets = []
    for i, p in enumerate(floor.get("floorPallets", [])):
        pallets.append({
            "id": f"FLOOR-{i:04d}",
            "width": p.get("width", 0),
            "height": p.get("height", 0),
            "depth": p.get("depth", 0),
            "weight": p.get("weight", 0),
            "type_size": p.get("typeSize", ""),
        })
    return pallets


def reset_sections(sections):
    """Сбросить состояние секций."""
    for s in sections:
        s["used_width"] = 0
        s["used_weight"] = 0
        s["used_pallets"] = 0
        s["placed_pallet_ids"] = []


# ============================================================================
# СТРАТЕГИИ СОРТИРОВКИ
# ============================================================================

def sort_by_height_weight(pallets):
    """Стратегия 1: Высокие → тяжёлые (текущая)."""
    return sorted(pallets, key=lambda p: (-p["height"], -p["weight"]))


def sort_by_popularity(pallets):
    """Стратегия 2: Популярные типоразмеры первыми."""
    # Подсчитать сколько паллет каждого типа
    type_counts = Counter()
    for p in pallets:
        key = (p["width"], p["height"], p["depth"], p["weight"])
        type_counts[key] += 1

    # Сортировать: популярные первыми, внутри типа — по высоте
    return sorted(pallets, key=lambda p: (
        -type_counts[(p["width"], p["height"], p["depth"], p["weight"])],
        -p["height"]
    ))


def sort_by_rarity(pallets, sections):
    """Стратегия 3: Редкие паллеты первыми (мало подходящих секций)."""
    def count_suitable_sections(pallet):
        count = 0
        for sec in sections:
            if pallet["height"] > sec["height"]:
                continue
            if pallet["depth"] > sec["depth"]:
                continue
            if sec["narrow_aisle"] and pallet["width"] > sec["max_width_pallet"]:
                continue
            count += 1
        return count

    # Кешируем подсчёт
    suitable_counts = {}
    for p in pallets:
        key = (p["width"], p["height"], p["depth"])
        if key not in suitable_counts:
            suitable_counts[key] = count_suitable_sections(p)

    # Сортировать: редкие первыми (мало подходящих секций)
    return sorted(pallets, key=lambda p: (
        suitable_counts[(p["width"], p["height"], p["depth"])],
        -p["height"]
    ))


def sort_hybrid_popular_rare(pallets, sections):
    """
    Стратегия 4: Гибридная — популярные + редкие по секциям.

    Логика:
    1. Популярные типоразмеры (>100 штук) — первыми
       → будут хорошо группироваться в секциях (гомогенное заполнение)
    2. Внутри популярных — редкие по секциям первыми
       → сложные паллеты не останутся без места
    """
    # Подсчитать популярность
    type_counts = Counter()
    for p in pallets:
        key = (p["width"], p["height"], p["depth"], p["weight"])
        type_counts[key] += 1

    # Подсчитать редкость (кол-во подходящих секций)
    def count_suitable_sections(pallet):
        count = 0
        for sec in sections:
            if pallet["height"] > sec["height"]:
                continue
            if pallet["depth"] > sec["depth"]:
                continue
            if sec["narrow_aisle"] and pallet["width"] > sec["max_width_pallet"]:
                continue
            count += 1
        return count

    suitable_counts = {}
    for p in pallets:
        key = (p["width"], p["height"], p["depth"])
        if key not in suitable_counts:
            suitable_counts[key] = count_suitable_sections(p)

    # Порог популярности
    POPULAR_THRESHOLD = 100

    # Сортировка:
    # 1. Популярные (>100шт) vs обычные
    # 2. Внутри группы — редкие по секциям первыми
    # 3. Внутри редкости — высокие первыми
    return sorted(pallets, key=lambda p: (
        type_counts[(p["width"], p["height"], p["depth"], p["weight"])] <= POPULAR_THRESHOLD,  # Популярные первыми (False < True)
        suitable_counts[(p["width"], p["height"], p["depth"])],  # Редкие первыми
        -p["height"]  # Высокие первыми
    ))


# ============================================================================
# СИМУЛЯЦИЯ РАЗМЕЩЕНИЯ
# ============================================================================

def find_suitable_section(pallet, sections):
    """Найти подходящую секцию."""
    candidates = []
    for sec in sections:
        if pallet["height"] > sec["height"]:
            continue
        if pallet["depth"] > sec["depth"]:
            continue
        if sec["narrow_aisle"] and pallet["width"] > sec["max_width_pallet"]:
            continue
        remaining_width = sec["width"] - sec["used_width"]
        if pallet["width"] + 50 > remaining_width:
            continue
        if sec["used_weight"] + pallet["weight"] > sec["max_weight"]:
            continue
        if sec["used_pallets"] >= sec["max_pallets"]:
            continue
        score = sec["used_pallets"]
        candidates.append((score, sec))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def place_pallet_simple(pallet, section):
    """Простое размещение."""
    section["used_width"] += pallet["width"] + 50
    section["used_weight"] += pallet["weight"]
    section["used_pallets"] += 1
    section["placed_pallet_ids"].append(pallet["id"])


def place_with_optimization(first_pallet, section, remaining_pallets):
    """Размещение с оптимизацией."""
    if section["used_pallets"] > 0:
        place_pallet_simple(first_pallet, section)
        return [first_pallet["id"]]

    type_counts = defaultdict(int)
    type_to_pallets = defaultdict(list)
    for p in remaining_pallets:
        key = (p["width"], p["height"], p["depth"], p["weight"])
        type_counts[key] += 1
        type_to_pallets[key].append(p)

    available_types = []
    for (w, h, d, wt), count in type_counts.items():
        available_types.append({
            "width": w,
            "height": h,
            "depth": d,
            "weight": wt,
            "count": count,
        })

    result = optimize_section_fill(section, available_types, gap_width=50.0)

    if not result:
        place_pallet_simple(first_pallet, section)
        return [first_pallet["id"]]

    placed_ids = []
    for sel in result:
        idx = sel["typeIndex"]
        count = sel["count"]
        ptype = available_types[idx]
        key = (ptype["width"], ptype["height"], ptype["depth"], ptype["weight"])
        pallets_of_type = type_to_pallets[key]
        for i in range(count):
            if i >= len(pallets_of_type):
                break
            p = pallets_of_type[i]
            place_pallet_simple(p, section)
            placed_ids.append(p["id"])

    return placed_ids


def run_simulation(strategy_name, sorted_pallets, sections):
    """Запустить симуляцию с заданной стратегией."""
    reset_sections(sections)

    placed_ids = set()
    not_placed = 0

    for pallet in sorted_pallets:
        if pallet["id"] in placed_ids:
            continue

        section = find_suitable_section(pallet, sections)
        if not section:
            not_placed += 1
            continue

        remaining = [p for p in sorted_pallets if p["id"] not in placed_ids]
        new_placed = place_with_optimization(pallet, section, remaining)
        placed_ids.update(new_placed)

    used_sections = [s for s in sections if s["used_pallets"] > 0]
    avg_util = sum(s["used_width"] / s["width"] for s in used_sections) / len(used_sections) if used_sections else 0

    fill_3 = sum(1 for s in used_sections if s["used_pallets"] == 3)
    fill_3_pct = (fill_3 / len(used_sections) * 100) if used_sections else 0

    return {
        "strategy": strategy_name,
        "placed": len(placed_ids),
        "not_placed": not_placed,
        "used_sections": len(used_sections),
        "avg_utilization": avg_util * 100,
        "sections_full": fill_3,
        "sections_full_pct": fill_3_pct,
    }


# ============================================================================
# ГЛАВНЫЙ ТЕСТ
# ============================================================================

def test_all_strategies():
    """Тест всех стратегий."""
    print("=" * 80)
    print("ТЕСТ СТРАТЕГИЙ СОРТИРОВКИ ПАЛЛЕТ")
    print("=" * 80)

    print("\nЗагружаем данные S7...")
    occupancy, floor = load_s7_data()
    sections = parse_sections(occupancy)
    pallets = parse_pallets(floor)

    print(f"  Секций: {len(sections)}")
    print(f"  Паллет: {len(pallets)}")

    strategies = [
        ("1. Высокие -> Тяжёлые (текущая)", lambda p, s: sort_by_height_weight(p)),
        ("2. Популярные типоразмеры первыми", lambda p, s: sort_by_popularity(p)),
        ("3. Редкие по секциям первыми", lambda p, s: sort_by_rarity(p, s)),
        ("4. Гибрид: Популярные + Редкие", lambda p, s: sort_hybrid_popular_rare(p, s)),
    ]

    results = []

    for name, sort_fn in strategies:
        print(f"\nЗапуск: {name}")
        sorted_pallets = sort_fn(pallets, sections)
        result = run_simulation(name, sorted_pallets, sections)
        results.append(result)
        print(f"   Размещено: {result['placed']}/{len(pallets)} ({result['placed']/len(pallets)*100:.1f}%)")

    # Таблица результатов
    print("\n" + "=" * 80)
    print("СРАВНЕНИЕ РЕЗУЛЬТАТОВ")
    print("=" * 80)
    print(f"\n{'Стратегия':<40} {'Размещено':<12} {'Утилизация':<12} {'3 паллеты'}")
    print("-" * 80)

    for r in results:
        placed_pct = f"{r['placed']}/{len(pallets)} ({r['placed']/len(pallets)*100:.1f}%)"
        util = f"{r['avg_utilization']:.1f}%"
        full = f"{r['sections_full']} ({r['sections_full_pct']:.1f}%)"
        print(f"{r['strategy']:<40} {placed_pct:<12} {util:<12} {full}")

    # Лучшая стратегия
    best = max(results, key=lambda r: r['placed'])
    print(f"\nЛучшая стратегия: {best['strategy']}")
    print(f"   Размещено на {best['placed'] - results[0]['placed']} паллет больше!")


if __name__ == "__main__":
    test_all_strategies()
