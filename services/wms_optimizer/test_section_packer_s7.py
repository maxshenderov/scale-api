"""
Тест локальной оптимизации секций на реальных данных S7.

Проверяем как работает section_packer.optimize_section_fill()
на реальных паллетах и секциях из S7 (3406 паллет, 1530 секций).
"""
import json
from collections import Counter
from optimizer.section_packer import optimize_section_fill


def load_s7_data():
    """Загрузить данные S7."""
    with open("tests/example/OccupancyS7.json", "r", encoding="utf-8") as f:
        occupancy = json.load(f)

    with open("tests/example/FloorS7.json", "r", encoding="utf-8") as f:
        floor = json.load(f)

    return occupancy, floor


def extract_pallet_types(floor_pallets):
    """Извлечь типоразмеры паллет."""
    types = []
    for p in floor_pallets:
        key = (p["width"], p["height"], p["depth"], p["weight"])
        types.append(key)
    return types


def extract_section_types(occupancy):
    """Извлечь типоразмеры секций."""
    sections = []
    for sec in occupancy.get("sections", []):
        # В S7 данные используют snake_case из 1С
        max_weight = sec.get("typeSize_weight")
        if sec.get("typeSize_unlimitedWeight"):
            max_weight = None

        sections.append({
            "id": sec.get("section_code", ""),
            "width": sec.get("typeSize_width", 0),
            "height": sec.get("typeSize_height", 0),
            "depth": sec.get("typeSize_depth", 0),
            "max_pallets": sec.get("max_pallets", 3),
            "max_weight": max_weight,
            "narrow_aisle": sec.get("narrowAisle", False),
            "max_width_pallet": sec.get("max_widthPallet"),
        })
    return sections


def simulate_section_packing():
    """
    Симуляция: проходим по секциям и пытаемся оптимально заполнить каждую.
    """
    print("Загружаем данные S7...")
    occupancy, floor = load_s7_data()

    floor_pallets = floor.get("floorPallets", [])  # Исправлено: floorPallets, не pallets
    sections = extract_section_types(occupancy)

    print(f"\nДанные:")
    print(f"  Секций: {len(sections)}")
    print(f"  Паллет с пола: {len(floor_pallets)}")

    # Группируем паллеты по типоразмерам
    pallet_type_counts = Counter()
    for p in floor_pallets:
        key = (p["width"], p["height"], p["depth"], p["weight"])
        pallet_type_counts[key] += 1

    print(f"  Уникальных типоразмеров паллет: {len(pallet_type_counts)}")

    # ТОП-10 типоразмеров паллет
    print("\nТОП-10 типоразмеров паллет:")
    for (w, h, d, wt), count in pallet_type_counts.most_common(10):
        print(f"  {w}×{h}×{d}мм, {wt}кг — {count} шт")

    # Группируем секции по типоразмерам
    section_type_counts = Counter()
    for sec in sections:
        key = (sec["width"], sec["height"], sec["depth"], sec["max_pallets"],
               sec.get("max_weight"), sec["narrow_aisle"])
        section_type_counts[key] += 1

    print(f"\n  Уникальных типоразмеров секций: {len(section_type_counts)}")

    # ТОП-5 типоразмеров секций
    print("\nТОП-5 типоразмеров секций:")
    for (w, h, d, mp, mw, na), count in section_type_counts.most_common(5):
        narrow = " [узкопроходная]" if na else ""
        print(f"  {w}×{h}×{d}мм, {mp} слота, {mw}кг{narrow} — {count} шт")

    # Берём одну типичную секцию
    typical_section = sections[0]
    print(f"\nТестируем на типичной секции:")
    print(f"  Ширина: {typical_section['width']}мм")
    print(f"  Высота: {typical_section['height']}мм")
    print(f"  Глубина: {typical_section['depth']}мм")
    print(f"  Макс паллет: {typical_section['max_pallets']}")
    print(f"  Макс вес: {typical_section['max_weight']}кг")

    # Формируем available_types из всех паллет
    available_types = []
    for (w, h, d, wt), count in pallet_type_counts.items():
        available_types.append({
            "width": w,
            "height": h,
            "depth": d,
            "weight": wt,
            "count": count
        })

    print(f"\nЗапускаем optimize_section_fill()...")
    result = optimize_section_fill(typical_section, available_types, gap_width=50.0)

    print(f"\nРезультат:")
    if not result:
        print("  ❌ Ничего не подобрано")
        return

    total_pallets = 0
    total_width = 0
    total_weight = 0

    for sel in result:
        idx = sel["typeIndex"]
        count = sel["count"]
        ptype = available_types[idx]

        width_for_type = ptype["width"] * count
        weight_for_type = ptype["weight"] * count

        total_pallets += count
        total_width += width_for_type
        total_weight += weight_for_type

        print(f"  Тип [{idx}]: {count} × {ptype['width']}мм = {width_for_type}мм, {weight_for_type}кг")

    gaps = 50 * (total_pallets + 1)
    total_with_gaps = total_width + gaps
    utilization = (total_width / typical_section['width']) * 100

    print(f"\n📊 Итого:")
    print(f"  Паллет: {total_pallets}")
    print(f"  Чистая ширина: {total_width}мм")
    print(f"  Зазоры: {gaps}мм ({total_pallets}+1 × 50мм)")
    print(f"  Всего с зазорами: {total_with_gaps}мм / {typical_section['width']}мм")
    print(f"  Утилизация: {utilization:.1f}%")
    print(f"  Вес: {total_weight}кг / {typical_section['max_weight']}кг")

    # Проверим на нескольких секциях разных типов
    print("\n" + "="*70)
    print("Тест на 5 разных типоразмерах секций:")
    print("="*70)

    tested_types = set()
    test_count = 0

    for sec in sections:
        if test_count >= 5:
            break

        sec_key = (sec["width"], sec["height"], sec["depth"])
        if sec_key in tested_types:
            continue

        tested_types.add(sec_key)
        test_count += 1

        print(f"\n{test_count}. Секция {sec['width']}×{sec['height']}×{sec['depth']}мм:")

        result = optimize_section_fill(sec, available_types, gap_width=50.0)

        if not result:
            print("   ❌ Ничего не подобрано")
            continue

        total_p = sum(s["count"] for s in result)
        total_w = sum(available_types[s["typeIndex"]]["width"] * s["count"] for s in result)
        util = (total_w / sec['width']) * 100

        print(f"   ✅ {total_p} паллет, {total_w}мм чистая ширина, {util:.1f}% утилизация")


if __name__ == "__main__":
    simulate_section_packing()
