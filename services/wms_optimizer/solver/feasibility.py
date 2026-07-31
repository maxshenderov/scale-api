"""Допустимые пары (паллета, секция) — общая логика для точной и агрегированной модели.

Правило: §7.3-7.5 (высота/глубина/грузоподъёмность) + eff_max_width/eff_max_depth
(потолок на одну паллету на узкопроходных стеллажах) + правило узкого прохода
(§7 — узкопроходная паллета допускается только в narrow_aisle секцию, если
strict_narrow=True). Текущая секция паллеты всегда остаётся допустимой для
правила узкого проходa — иначе существующая паллета, уже стоящая в секции без
narrow_aisle, выпадает из модели целиком и её вес/ширина не учитываются в
ограничениях секции (§7.1-7.2).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from models.pallet import Pallet
from models.section import Section


def pallet_fits_section(pallet: Pallet, sec: Section) -> bool:
    return (
        pallet.height <= sec.height
        and pallet.depth <= sec.depth
        and pallet.weight <= sec.max_lift_weight
        and pallet.width <= sec.eff_max_width
        and pallet.depth <= sec.eff_max_depth
    )


def compute_feasible_pairs(
    pallets: List[Pallet],
    sections: List[Section],
    strict_narrow: bool,
    pallet_current_section: Dict[str, str],
    section_idx: Dict[str, int],
) -> Dict[str, List[int]]:
    """{pallet_id: [индексы допустимых секций в списке sections]}."""
    feasible: Dict[str, List[int]] = {}
    for p in pallets:
        feasible[p.id] = []
        current_sec_idx: Optional[int] = section_idx.get(pallet_current_section.get(p.id))
        for i, sec in enumerate(sections):
            if strict_narrow and p.is_narrow and not sec.narrow_aisle and i != current_sec_idx:
                continue
            if pallet_fits_section(p, sec):
                feasible[p.id].append(i)
    return feasible


def count_pairs(feasible: Dict[str, List[int]]) -> int:
    return sum(len(v) for v in feasible.values())
