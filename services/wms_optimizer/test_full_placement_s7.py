"""
Полная симуляция размещения 3406 паллет S7 с локальной оптимизацией.

Алгоритм:
1. Сортируем паллеты по убыванию высоты (высокие первыми)
2. Для каждого паллета:
   - Ищем подходящую секцию (по высоте, глубине, весу)
   - Если секция пустая → запускаем optimize_section_fill()
   - Размещаем выбранную комбинацию
   - Обновляем состояние секции
3. Считаем метрики: размещено, отказано, утилизация
"""
import json
from collections import defaultdict
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
            max_weight = 999999  # Неограничен

        sections.append({
            "id": sec.get("section_code", ""),
            "width": sec.get("typeSize_width", 0),
            "height": sec.get("typeSize_height", 0),
            "depth": sec.get("typeSize_depth", 0),
            "max_pallets": sec.get("max_pallets", 3),
            "max_weight": max_weight,
            "narrow_aisle": sec.get("narrowAisle", False),
            "max_width_pallet": sec.get("max_widthPallet", 1200),
            # Текущее состояние
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


def sort_pallets(pallets, sections):
    """
    Сортировка паллет: редкие по секциям первыми.

    "Редкий" = мало подходящих секций → размещаем первым,
    иначе может не остаться подходящих мест.
    """
    def count_suitable_sections(pallet):
        count = 0
        for sec in sections:
            if pallet["height"] > sec["height"]:
                continue
            if pallet["depth"] > sec["depth"]:
                continue
            if sec["narrow_aisle"] and pallet["width"] > sec.get("max_width_pallet", 1200):
                continue
            count += 1
        return count

    # Кешируем подсчёт для каждого типоразмера
    suitable_counts = {}
    for p in pallets:
        key = (p["width"], p["height"], p["depth"])
        if key not in suitable_counts:
            suitable_counts[key] = count_suitable_sections(p)

    # Сортировать: редкие первыми (мало секций), внутри — высокие первыми
    return sorted(pallets, key=lambda p: (
        suitable_counts[(p["width"], p["height"], p["depth"])],
        -p["height"]
    ))


def find_suitable_section(pallet, sections):
    """
    Найти подходящую секцию для паллета.

    Приоритет:
    1. Частично занятые секции (есть место)
    2. Пустые секции
    """
    candidates = []

    for sec in sections:
        # Проверка габаритов
        if pallet["height"] > sec["height"]:
            continue
        if pallet["depth"] > sec["depth"]:
            continue

        # Узкопроходность
        if sec["narrow_aisle"] and pallet["width"] > sec["max_width_pallet"]:
            continue

        # Проверка остатка места
        remaining_width = sec["width"] - sec["used_width"]
        if pallet["width"] + 50 > remaining_width:  # +50мм зазор
            continue

        # Проверка веса
        if sec["used_weight"] + pallet["weight"] > sec["max_weight"]:
            continue

        # Проверка слотов
        if sec["used_pallets"] >= sec["max_pallets"]:
            continue

        # Кандидат подходит
        score = sec["used_pallets"]  # Предпочитаем частично занятые
        candidates.append((score, sec))

    if not candidates:
        return None

    # Сортируем: частично занятые первыми
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def place_pallet_simple(pallet, section):
    """Простое размещение одного паллета."""
    section["used_width"] += pallet["width"] + 50  # +зазор
    section["used_weight"] += pallet["weight"]
    section["used_pallets"] += 1
    section["placed_pallet_ids"].append(pallet["id"])


def place_with_optimization(first_pallet, section, remaining_pallets):
    """
    Размещение с оптимизацией:
    - Если секция пустая → optimize_section_fill()
    - Иначе → простое размещение
    """
    if section["used_pallets"] > 0:
        # Секция уже занята → простое размещение
        place_pallet_simple(first_pallet, section)
        return [first_pallet["id"]]

    # Секция пустая → оптимизируем заполнение
    # Формируем available_types из оставшихся паллет
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

    # Запускаем оптимизацию
    result = optimize_section_fill(section, available_types, gap_width=50.0)

    if not result:
        # Оптимизация не нашла решения → простое размещение
        place_pallet_simple(first_pallet, section)
        return [first_pallet["id"]]

    # Размещаем выбранные паллеты
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


def simulate_placement():
    """Полная симуляция размещения."""
    print("=" * 70)
    print("ПОЛНАЯ СИМУЛЯЦИЯ РАЗМЕЩЕНИЯ S7 С ЛОКАЛЬНОЙ ОПТИМИЗАЦИЕЙ")
    print("=" * 70)

    print("\nЗагружаем данные...")
    occupancy, floor = load_s7_data()

    sections = parse_sections(occupancy)
    pallets = parse_pallets(floor)

    print(f"  Секций: {len(sections)}")
    print(f"  Паллет: {len(pallets)}")

    print("\nСортируем паллеты (редкие по секциям первыми)...")
    sorted_pallets = sort_pallets(pallets, sections)

    print("\nЗапускаем размещение...\n")

    placed_ids = set()
    not_placed = []
    not_placed_reasons = defaultdict(int)

    total = len(sorted_pallets)
    checkpoint = total // 10  # Каждые 10%

    for i, pallet in enumerate(sorted_pallets):
        if (i + 1) % checkpoint == 0:
            progress = ((i + 1) / total) * 100
            print(f"  Прогресс: {i + 1}/{total} ({progress:.0f}%) — размещено {len(placed_ids)}")

        if pallet["id"] in placed_ids:
            continue  # Уже размещён через оптимизацию

        # Найти подходящую секцию
        section = find_suitable_section(pallet, sections)

        if not section:
            not_placed.append(pallet)
            # Определить причину отказа
            reason = "NO_SUITABLE_SECTION"
            for sec in sections:
                if pallet["height"] > sec["height"]:
                    reason = "HEIGHT_LIMIT"
                    break
                if sec["narrow_aisle"] and pallet["width"] > sec["max_width_pallet"]:
                    reason = "NARROW_AISLE_MISMATCH"
                    break
            not_placed_reasons[reason] += 1
            continue

        # Получить оставшиеся паллеты (ещё не размещённые)
        remaining = [p for p in sorted_pallets if p["id"] not in placed_ids]

        # Разместить с оптимизацией
        new_placed = place_with_optimization(pallet, section, remaining)
        placed_ids.update(new_placed)

    print(f"\n✅ Размещение завершено!\n")

    # Метрики
    print("=" * 70)
    print("📊 РЕЗУЛЬТАТЫ")
    print("=" * 70)
    print(f"\n✅ Размещено: {len(placed_ids)}/{total} ({len(placed_ids)/total*100:.1f}%)")
    print(f"❌ Не размещено: {len(not_placed)}/{total} ({len(not_placed)/total*100:.1f}%)")

    if not_placed_reasons:
        print(f"\nПричины отказа:")
        for reason, count in sorted(not_placed_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")

    # Утилизация секций
    used_sections = [s for s in sections if s["used_pallets"] > 0]
    print(f"\nИспользовано секций: {len(used_sections)}/{len(sections)}")

    if used_sections:
        avg_pallets = sum(s["used_pallets"] for s in used_sections) / len(used_sections)
        avg_width_util = sum(s["used_width"] / s["width"] for s in used_sections) / len(used_sections)
        print(f"  Средняя заполненность: {avg_pallets:.2f} паллет/секция")
        print(f"  Средняя утилизация ширины: {avg_width_util*100:.1f}%")

        # Распределение по заполненности
        fill_dist = defaultdict(int)
        for s in used_sections:
            fill_dist[s["used_pallets"]] += 1

        print(f"\nРаспределение по слотам:")
        for slots in sorted(fill_dist.keys()):
            count = fill_dist[slots]
            pct = (count / len(used_sections)) * 100
            print(f"  {slots} паллет: {count} секций ({pct:.1f}%)")


if __name__ == "__main__":
    simulate_placement()
