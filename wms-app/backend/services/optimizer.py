"""Алгоритм размещения партии паллет (Bin Packing + Reslotting).

Работает БЕЗ 1С — получает данные о секциях и паллетах,
возвращает план размещения. Ограничения те же что в 1С:
габариты, зазоры, структура адресов (широкий/средний/узкий).
"""

from typing import List, Dict, Optional


class Section:
    """Секция стеллажа (контейнер для паллет)."""

    def __init__(self, id: str, width: float, height: float, depth: float,
                 max_weight: float = 0, rack_id: str = "", floor: int = 0):
        self.id = id
        self.width = width
        self.height = height
        self.depth = depth
        self.max_weight = max_weight  # 0 = безлимит
        self.rack_id = rack_id
        self.floor = floor
        self.occupied = []  # список паллет в этой секции
        self.addresses = [None, None, None]  # Address1, Address2, Address3

    def is_empty(self) -> bool:
        return len(self.occupied) == 0

    def n_occupied(self) -> int:
        return len(self.occupied)

    def remaining_weight(self) -> float:
        if self.max_weight == 0:
            return float('inf')
        used = sum(p.get('weight', 0) for p in self.occupied)
        return self.max_weight - used

    def can_fit(self, pallet: dict, floor_params: dict, width_clearance: float) -> bool:
        """Проверка базовых ограничений."""
        p_width = pallet.get('width', 0)
        p_height = pallet.get('height', 0)
        p_depth = pallet.get('depth', 1100)
        p_weight = pallet.get('weight', 0)

        if p_height > self.height or p_depth > self.depth:
            return False
        if self.remaining_weight() < p_weight:
            return False
        if floor_params.get('maxLiftWeight', 0) > 0:
            if p_weight > floor_params.get('maxLiftWeight', 0):
                return False
        return True

    def remaining_width(self) -> float:
        """Оставшаяся ширина с учётом зазоров."""
        used = sum(p.get('width', 0) for p in self.occupied)
        # Зазор: (N+1) × widthClearance между всеми паллетами
        n = len(self.occupied) + 1  # +1 для новой паллеты
        clearance = n * 20  # default 20мм зазор
        return self.width - used - clearance


class Pallet:
    """Паллета (предмет упаковки)."""

    def __init__(self, data: dict):
        self.id = data.get('id', '')
        self.width = data.get('width', 0)
        self.height = data.get('height', 0)
        self.depth = data.get('depth', 1100)
        self.weight = data.get('weight', 0)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'width': self.width,
            'height': self.height,
            'depth': self.depth,
            'weight': self.weight
        }

    def category(self, sec_width: float) -> str:
        """Категория паллеты: wide / medium / narrow."""
        if self.width > sec_width * 2 / 3:
            return 'wide'
        elif self.width > sec_width / 3:
            return 'medium'
        else:
            return 'narrow'


def place_pallet_batch(pallets_data: list, sections_data: list = None,
                      floor_params: dict = None) -> dict:
    """
    Размещает партию паллет в секциях (Bin Packing).

    На входе:
    - pallets_data: [{id, width, height, depth, weight}, ...]
    - sections_data: [{id, width, height, depth, weight, rack, floor}, ...]
    - floor_params: {widthClearance, maxLiftWeight}

    Возвращает: {newPlacements, unplaced, stats}
    """

    if not pallets_data:
        return {
            "newPlacements": [],
            "unplaced": [],
            "stats": {
                "total": 0,
                "placed": 0,
                "movesUsed": 0,
                "sectionsFreedUp": 0
            }
        }

    # Default значения
    if floor_params is None:
        floor_params = {'widthClearance': 20, 'maxLiftWeight': 0}

    # Если нет секций — создаём виртуальные
    if not sections_data:
        sections_data = _create_virtual_sections(len(pallets_data))

    # Создаём объекты
    pallets = [Pallet(p) for p in pallets_data]
    sections = [Section(
        s.get('id', f"sec-{i}"),
        s.get('width', 2700),
        s.get('height', 1800),
        s.get('depth', 1100),
        s.get('weight', 0),
        s.get('rack', ''),
        s.get('floor', 0)
    ) for i, s in enumerate(sections_data)]

    # Сортируем по убыванию ширины (BFD)
    pallets.sort(key=lambda p: p.width, reverse=True)

    sec_width = sections[0].width if sections else 2700

    # Фаза 1: Широкие паллеты (> 2W/3)
    wide = [p for p in pallets if p.width > sec_width * 2 / 3]
    medium = [p for p in pallets if sec_width / 3 < p.width <= sec_width * 2 / 3]
    narrow = [p for p in pallets if p.width <= sec_width / 3]

    placements = []
    unplaced = []

    # Размещаем широкие в пустые секции
    empty_sections = [s for s in sections if s.is_empty()]
    for pallet in sorted(wide, key=lambda p: p.width, reverse=True):
        candidates = [s for s in empty_sections if pallet.can_fit(pallet.to_dict(), floor_params, floor_params.get('widthClearance', 20))]
        if candidates:
            # Best-fit по высоте
            best = min(candidates, key=lambda s: s.height - pallet.height)
            best.occupied.append(pallet.to_dict())
            best.addresses[1] = pallet.id  # занимаем все 3 адреса
            placements.append({
                "pallet": pallet.id,
                "section": best.id,
                "address": best.id,
                "rack": best.rack_id,
                "floor": best.floor
            })
            empty_sections.remove(best)
        else:
            unplaced.append({"pallet": pallet.id, "reason": "No suitable section for wide pallet"})

    # Размещаем средние (Address2 + крайний)
    for pallet in sorted(medium, key=lambda p: p.width, reverse=True):
        candidates = [s for s in sections if not s.is_empty() and s.n_occupied() < 3]
        if candidates:
            # Лучший fit по оставшейся ширине
            candidates = [s for s in candidates if pallet.can_fit(pallet.to_dict(), floor_params, floor_params.get('widthClearance', 20))]
            if candidates:
                best = min(candidates, key=lambda s: abs(s.remaining_width() - pallet.width))
                best.occupied.append(pallet.to_dict())
                placements.append({
                    "pallet": pallet.id,
                    "section": best.id,
                    "address": best.id,
                    "rack": best.rack_id,
                    "floor": best.floor
                })

        if not any(p['pallet'] == pallet.id for p in placements):
            unplaced.append({"pallet": pallet.id, "reason": "No suitable section for medium pallet"})

    # Размещаем узкие (добивка до 3)
    for pallet in sorted(narrow, key=lambda p: p.width, reverse=True):
        # Ищем частично занятые секции
        candidates = [s for s in sections if 0 < s.n_occupied() < 3]
        if candidates:
            # Приоритет добивке (максимально занятой секции)
            candidates = [s for s in candidates if pallet.can_fit(pallet.to_dict(), floor_params, floor_params.get('widthClearance', 20))]
            if candidates:
                best = max(candidates, key=lambda s: s.n_occupied())  # самая занятая
                best.occupied.append(pallet.to_dict())
                placements.append({
                    "pallet": pallet.id,
                    "section": best.id,
                    "address": best.id,
                    "rack": best.rack_id,
                    "floor": best.floor
                })

        if not any(p['pallet'] == pallet.id for p in placements):
            unplaced.append({"pallet": pallet.id, "reason": "No suitable section for narrow pallet"})

    # Статистика
    total = len(pallets)
    placed = len(placements)

    return {
        "newPlacements": placements,
        "reslotMoves": [],  # TODO: реслотинг
        "unplaced": unplaced,
        "stats": {
            "total": total,
            "placed": placed,
            "density": placed / total if total > 0 else 0,
            "movesUsed": 0,
            "sectionsFreedUp": 0
        }
    }


def _create_virtual_sections(n_pallets: int) -> list:
    """Создаёт виртуальные секции для демонстрации."""
    sections = []
    for i in range((n_pallets + 2) // 3):  # примерно по 3 паллеты в секции
        sections.append({
            'id': f'sec-{i+1}',
            'width': 2700,
            'height': 1800,
            'depth': 1100,
            'weight': 0,
            'rack': f'rack-{i % 9 + 1}',
            'floor': (i // 9) + 1
        })
    return sections
