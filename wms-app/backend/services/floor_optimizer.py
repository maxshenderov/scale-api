"""Оптимизация высот этажей стеллажа.

Анализирует распределение паллет по высотам и подбирает
оптимальные типоразмеры секций из существующего каталога.
"""


def build_floor_report(rack: dict, sections: list) -> dict:
    """Строит отчёт по этажам одного стеллажа."""
    floors = rack.get("floors", [])
    rack_width = floors[0]["typeSize"]["width"] if floors else 2700
    sections_count = rack.get("sectionsCount", 17)

    # Собираем паллеты по этажам
    floor_pallets = {}
    for sec in sections:
        fn = sec.get("floor", 0)
        if fn not in floor_pallets:
            floor_pallets[fn] = []
        for addr in sec.get("addresses", []):
            if addr.get("pallet") and addr.get("height"):
                floor_pallets[fn].append({
                    "height": addr["height"],
                    "width": addr.get("width", 0),
                    "weight": addr.get("weight", 0),
                })

    report = []
    for floor in floors:
        fn = floor["number"]
        pallets = floor_pallets.get(fn, [])
        section_height = floor["typeSize"]["height"]

        if pallets:
            avg_h = sum(p["height"] for p in pallets) / len(pallets)
            wasted = section_height - avg_h
            utilization = (sum(p["height"] for p in pallets) /
                          (sections_count * 3 * section_height) * 100)
        else:
            avg_h = 0
            wasted = 0
            utilization = 0

        is_fixed = floor.get("heightFromFloorFixed", False)

        entry = {
            "floor": fn,
            "currentHeight": section_height,
            "avgPalletHeight": round(avg_h),
            "palletsCount": len(pallets),
            "utilizationPercent": round(utilization, 1),
            "wastedMm": round(wasted),
            "fixed": is_fixed,
            "fixedReason": "ВысотаОтПола фиксирована" if is_fixed else None,
        }

        if not is_fixed:
            entry["recommended"] = {
                "height": section_height,
                "name": str(section_height),
            }

        report.append(entry)

    return {
        "rackId": rack.get("id", ""),
        "rackName": rack.get("name", ""),
        "rackWidth": rack_width,
        "totalFloors": len(floors),
        "segments": 1,
        "fixedFloorCount": sum(1 for r in report if r["fixed"]),
        "floors": report,
        "summary": {
            "currentUtilization": round(
                sum(r["utilizationPercent"] for r in report) / max(len(report), 1), 1
            ),
            "optimizableFloors": sum(1 for r in report if not r["fixed"]),
        }
    }
