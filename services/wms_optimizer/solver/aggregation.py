"""Агрегация паллет и секций по типоразмерам для CP-SAT модели.

Ключевая идея: паллеты одного типоразмера взаимозаменяемы при размещении в секции
того же типа. Вместо X[паллета_id, секция_id] (булева переменная) используем
Y[тип_паллеты, тип_секции] (целочисленная переменная-счётчик).

Сокращает число переменных с O(N_паллет × N_секций) до O(T_паллет × T_секций),
где T — число уникальных типоразмеров (обычно 20-50 против тысяч экземпляров).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from models.address import Address
from models.pallet import Pallet
from models.section import Section
from solver.feasibility import pallet_fits_section

logger = logging.getLogger(__name__)


def pallet_type_key(p: Pallet) -> Tuple[float, float, float, float, bool]:
    """Ключ типоразмера паллеты.

    Паллеты с одинаковым ключом взаимозаменяемы при размещении.
    """
    return (
        round(p.width, 1),
        round(p.height, 1),
        round(p.depth, 1),
        round(p.weight, 1),
        p.is_narrow,
    )


def section_type_key(
    sec: Section,
    used_slots: int,
    used_width: float,
    used_weight: float,
) -> Tuple:
    """Ключ типоразмера секции С УЧЁТОМ текущей занятости.

    Две физически одинаковые секции с разной занятостью НЕ взаимозаменяемы —
    у них разная оставшаяся вместимость.

    Args:
        sec: Секция
        used_slots: Занято слотов (0-3)
        used_width: Использовано ширины (мм)
        used_weight: Использовано веса (кг)
    """
    remaining_slots = sec.max_pallets - used_slots
    remaining_width = sec.width - used_width - sec.gap_width  # -gap для первого паллета
    remaining_weight = sec.max_weight - used_weight

    # Округляем чтобы избежать микро-различий из-за float
    return (
        round(sec.width, 1),
        round(sec.height, 1),
        round(sec.depth, 1),
        round(sec.max_weight, 1) if sec.max_weight != float('inf') else -1,
        sec.narrow_aisle,
        round(sec.eff_max_width, 1),
        round(sec.eff_max_depth, 1),
        remaining_slots,
        round(remaining_width, 1),
        round(remaining_weight, 1),
    )


@dataclass
class PalletType:
    """Агрегированный тип паллет."""
    key: Tuple
    width: float
    height: float
    depth: float
    weight: float
    is_narrow: bool
    count: int  # Количество паллет этого типа
    pallet_ids: List[str]  # ID конкретных паллет для дезагрегации


@dataclass
class SectionType:
    """Агрегированный тип секций."""
    key: Tuple
    width: float
    height: float
    depth: float
    max_weight: float
    narrow_aisle: bool
    eff_max_width: float
    eff_max_depth: float
    gap_width: float
    remaining_slots: int
    remaining_width: float
    remaining_weight: float
    count: int  # Количество секций этого типа
    section_indices: List[int]  # Индексы конкретных секций для дезагрегации


def build_type_aggregation(
    pallets: List[Pallet],
    sections: List[Section],
    addresses: List[Address],
    strict_narrow: bool,
    pallet_current_section: Dict[str, str],
    section_idx: Dict[str, int],
) -> Tuple[List[PalletType], List[SectionType], Set[Tuple[int, int]]]:
    """Построить агрегацию по типоразмерам.

    Returns:
        pallet_types: Список уникальных типов паллет
        section_types: Список уникальных типов секций
        feasible_type_pairs: Множество допустимых пар (ptype_idx, stype_idx)
    """
    # Вычислить текущую занятость секций
    section_occupancy: Dict[str, Dict[str, float]] = {
        s.id: {"slots": 0, "width": 0.0, "weight": 0.0} for s in sections
    }

    existing_map = {p.id: p for p in pallets if p.current_section_id is not None}
    for addr in addresses:
        if addr.pallet_id and addr.pallet_id in existing_map:
            p = existing_map[addr.pallet_id]
            occ = section_occupancy[addr.section_id]
            occ["slots"] += 1
            occ["width"] += p.width + sections[section_idx[addr.section_id]].gap_width
            occ["weight"] += p.weight

    # Группировка паллет по типоразмерам
    pallet_groups: Dict[Tuple, List[Pallet]] = {}
    for p in pallets:
        key = pallet_type_key(p)
        if key not in pallet_groups:
            pallet_groups[key] = []
        pallet_groups[key].append(p)

    pallet_types = [
        PalletType(
            key=key,
            width=pallets[0].width,
            height=pallets[0].height,
            depth=pallets[0].depth,
            weight=pallets[0].weight,
            is_narrow=pallets[0].is_narrow,
            count=len(pallets),
            pallet_ids=[p.id for p in pallets],
        )
        for key, pallets in pallet_groups.items()
    ]

    # Группировка секций по типоразмерам (с учётом занятости)
    section_groups: Dict[Tuple, List[Tuple[Section, int]]] = {}
    for i, sec in enumerate(sections):
        occ = section_occupancy[sec.id]
        key = section_type_key(sec, occ["slots"], occ["width"], occ["weight"])
        if key not in section_groups:
            section_groups[key] = []
        section_groups[key].append((sec, i))

    section_types = [
        SectionType(
            key=key,
            width=secs[0][0].width,
            height=secs[0][0].height,
            depth=secs[0][0].depth,
            max_weight=secs[0][0].max_weight,
            narrow_aisle=secs[0][0].narrow_aisle,
            eff_max_width=secs[0][0].eff_max_width,
            eff_max_depth=secs[0][0].eff_max_depth,
            gap_width=secs[0][0].gap_width,
            remaining_slots=key[7],  # из section_type_key
            remaining_width=key[8],
            remaining_weight=key[9],
            count=len(secs),
            section_indices=[idx for _, idx in secs],
        )
        for key, secs in section_groups.items()
    ]

    logger.info(
        "Агрегация: %d паллет → %d типов, %d секций → %d типов",
        len(pallets), len(pallet_types), len(sections), len(section_types),
    )

    # Допустимые пары типов
    feasible_type_pairs: Set[Tuple[int, int]] = set()

    for ptype_idx, ptype in enumerate(pallet_types):
        # Берём первую паллету типа как представителя
        representative_pallet = next(p for p in pallets if pallet_type_key(p) == ptype.key)
        current_sec_idx_rep = section_idx.get(pallet_current_section.get(representative_pallet.id))

        for stype_idx, stype in enumerate(section_types):
            # Проверяем физическую совместимость
            if representative_pallet.height > stype.height:
                continue
            if representative_pallet.depth > stype.depth:
                continue
            if representative_pallet.weight > stype.remaining_weight / stype.count:
                continue
            if representative_pallet.width > stype.eff_max_width:
                continue
            if representative_pallet.depth > stype.eff_max_depth:
                continue

            # Правило узкого прохода
            if strict_narrow and representative_pallet.is_narrow and not stype.narrow_aisle:
                # Проверяем есть ли текущая секция в этом типе
                if current_sec_idx_rep not in stype.section_indices:
                    continue

            feasible_type_pairs.add((ptype_idx, stype_idx))

    logger.info("Допустимых пар типов: %d (теор. макс %d)",
                len(feasible_type_pairs), len(pallet_types) * len(section_types))

    return pallet_types, section_types, feasible_type_pairs


def disaggregate_solution(
    Y_solution: Dict[Tuple[int, int], int],
    pallet_types: List[PalletType],
    section_types: List[SectionType],
    pallets: List[Pallet],
    sections: List[Section],
) -> Dict[str, str]:
    """Дезагрегация решения CP-SAT: распределить конкретные паллеты по секциям.

    Args:
        Y_solution: {(ptype_idx, stype_idx): количество} — решение агрегированной модели
        pallet_types: Типы паллет
        section_types: Типы секций
        pallets: Список всех паллет
        sections: Список всех секций

    Returns:
        assignment: {pallet_id: section_id} — конкретное размещение
    """
    assignment: Dict[str, str] = {}

    # Индексы для быстрого доступа
    pallet_map = {p.id: p for p in pallets}
    section_map = {s.id: s for s in sections}

    # Отслеживание состояния секций во время дезагрегации
    section_state: Dict[str, Dict] = {
        s.id: {
            "used_slots": 0,
            "used_width": 0.0,
            "used_weight": 0.0,
        }
        for s in sections
    }

    # Обрабатываем каждую пару (тип_паллеты, тип_секции) с Y > 0
    for (ptype_idx, stype_idx), count in Y_solution.items():
        if count <= 0:
            continue

        ptype = pallet_types[ptype_idx]
        stype = section_types[stype_idx]

        # Берём count паллет этого типа
        pallets_to_place = [
            pallet_map[pid] for pid in ptype.pallet_ids[:count]
            if pid not in assignment  # Ещё не размещены
        ]

        # Берём секции этого типа с оставшимся местом
        available_sections = []
        for sec_idx in stype.section_indices:
            sec = sections[sec_idx]
            state = section_state[sec.id]
            if state["used_slots"] < sec.max_pallets:
                # Сортируем по убыванию занятости (заполняем частично занятые первыми)
                available_sections.append((state["used_slots"], sec))

        available_sections.sort(key=lambda x: -x[0])  # Больше занятых — первыми

        # Жадно размещаем паллеты
        sec_pool_idx = 0
        for p in pallets_to_place:
            if sec_pool_idx >= len(available_sections):
                logger.warning(
                    "Дезагрегация: не хватает секций типа %d для паллеты %s",
                    stype_idx, p.id
                )
                break

            _, sec = available_sections[sec_pool_idx]
            state = section_state[sec.id]

            # Проверяем вместимость
            remaining_width = sec.width - state["used_width"] - sec.gap_width
            if p.width + sec.gap_width > remaining_width or \
               state["used_weight"] + p.weight > sec.max_weight:
                # Переходим к следующей секции
                sec_pool_idx += 1
                if sec_pool_idx >= len(available_sections):
                    logger.warning(
                        "Дезагрегация: не хватает вместимости для паллеты %s", p.id
                    )
                    break
                _, sec = available_sections[sec_pool_idx]
                state = section_state[sec.id]

            # Размещаем
            assignment[p.id] = sec.id
            state["used_slots"] += 1
            state["used_width"] += p.width + sec.gap_width
            state["used_weight"] += p.weight

    logger.info("Дезагрегация: размещено %d/%d паллет",
                len(assignment), len(pallets))

    return assignment
