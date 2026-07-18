"""Алгоритм размещения партии паллет (Bin Packing / Рюкзак).

Работает БЕЗ 1С — получает данные о секциях и паллетах,
возвращает план размещения. Ограничения те же что в 1С:
габариты, зазоры, структура адресов (широкий/средний/узкий).
"""


def place_pallet_batch(pallets: list, sections: list = None) -> dict:
    """
    Размещает партию паллет.

    На входе: список паллет [{id, width, height, depth, weight}, ...]
    Возвращает: {newPlacements, unplaced, stats}

    Фаза 1 (MVP): без реальных секций — возвращаем заглушку,
    чтобы фронтенд мог разрабатываться. Полный BFD будет
    подключён когда появится загрузка топологии из 1С/снимка.
    """
    import uuid

    if not pallets:
        return {"newPlacements": [], "unplaced": [], "stats": {"total": 0, "placed": 0}}

    # MVP: заглушка — распределяем паллеты по виртуальным секциям
    sorted_pallets = sorted(pallets, key=lambda p: p.width if hasattr(p, 'width') else p.get('width', 0), reverse=True)

    placements = []
    unplaced = []

    for i, p in enumerate(sorted_pallets):
        w = p.width if hasattr(p, 'width') else p.get('width', 0)
        h = p.height if hasattr(p, 'height') else p.get('height', 0)
        pid = p.id if hasattr(p, 'id') else p.get('id', str(uuid.uuid4()))

        placements.append({
            "pallet": str(pid),
            "section": f"section-{i // 3 + 1}",
            "address": f"addr-{i + 1}",
            "rack": f"rack-{i % 9 + 1}",
            "floor": (i % 9) + 1,
            "fillLevel": 1 if i % 3 == 2 else (2 if i % 3 == 1 else 4),
        })

    total = len(pallets)
    placed = len(placements)

    return {
        "newPlacements": placements,
        "reslotMoves": [],
        "unplaced": unplaced,
        "stats": {
            "total": total,
            "placed": placed,
            "newPlaced": placed,
            "movesUsed": 0,
            "movesLimit": 0,
            "sectionsFreedUp": 0,
            "densityGainPercent": 0,
        }
    }
