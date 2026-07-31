"""Hybrid V9: Greedy Section-First Packing — детерминированный, без LNS/CP-SAT.

Алгоритм:
  1. Multi-start BFD с разными стратегиями сортировки паллет → лучший результат
  2. Greedy section-first: заполняем частично-заполненные секции оптимально
  3. Для остатков — подбираем лучшую пустую секцию и заполняем
  4. Chain-swap (depth-1) для остатков + безопасный реслот
"""
import time
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from api.schemas import (
    NewPalletSchema,
    OccupancySectionSchema,
    OperationSchema,
    NotPlacedSchema,
    MetricsSchema,
    OptimizationRequest,
    OptimizationResponse,
    OptimizationSettingsSchema,
    SolverStatus,
    PlacementStatus,
)
from models.occupancy_builder import build_warehouse_state
from models.pallet import Pallet, PalletTypeSize
from models.section import Section
from optimizer.potential import section_fits_pallet

logger = logging.getLogger(__name__)


@dataclass
class _SectionState:
    section: Section
    free_width: float
    free_count: int
    placed_pallets: List[Pallet] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.section.id

    @property
    def gap_width(self) -> float:
        return self.section.gap_width


# ---------------------------------------------------------------------------
# Multi-start BFD стратегии
# ---------------------------------------------------------------------------

def _bfd_sort_strategy_1(pallets: List[Pallet]) -> List[Tuple]:
    """V3-совместимая: группировка по 5-tuple, narrow first, descending height×width×weight."""
    groups: Dict[tuple, List[Pallet]] = defaultdict(list)
    for p in pallets:
        key = (p.is_narrow, p.height, p.width, p.depth, p.weight)
        groups[key].append(p)
    sorted_keys = sorted(groups.keys(), key=lambda k: (not k[0], -k[1], -k[2], -k[4]))
    result = []
    for key in sorted_keys:
        result.extend(groups[key])
    return result


def _bfd_sort_strategy_2(pallets: List[Pallet]) -> List[Tuple]:
    """Most-constrained-first: по числу совместимых секций, затем width, weight."""
    return sorted(pallets, key=lambda p: (-p.width, -p.weight))


def _bfd_sort_strategy_3(pallets: List[Pallet]) -> List[Tuple]:
    """Width-descending: широкие первыми, затем высота, вес."""
    return sorted(pallets, key=lambda p: (-p.width, -p.height, -p.weight))


def _bfd_sort_strategy_4(pallets: List[Pallet]) -> List[Tuple]:
    """Narrow-first, then group by height — внутри группы: width desc, then weight desc."""
    groups: Dict[tuple, List[Pallet]] = defaultdict(list)
    for p in pallets:
        key = (p.is_narrow, p.height)
        groups[key].append(p)
    sorted_keys = sorted(groups.keys(), key=lambda k: (not k[0], -k[1]))
    result = []
    for key in sorted_keys:
        result.extend(sorted(groups[key], key=lambda p: (-p.width, -p.weight)))
    return result


def _bfd_sort_strategy_5(pallets: List[Pallet]) -> List[Tuple]:
    """Narrow-first, group by (is_narrow, width_bucket) — W>1000 first, then W≤800 last."""
    groups: Dict[tuple, List[Pallet]] = defaultdict(list)
    for p in pallets:
        bucket = 0 if p.width > 1000 else (1 if p.width > 800 else 2)
        key = (p.is_narrow, bucket, p.height)
        groups[key].append(p)
    # Широкие (>1000) первыми, потом средние (800-1000), потом узкие (≤800)
    sorted_keys = sorted(groups.keys(), key=lambda k: (not k[0], k[1], -k[2]))
    result = []
    for key in sorted_keys:
        result.extend(sorted(groups[key], key=lambda p: (-p.width, -p.weight)))
    return result


def _bfd_sort_strategy_6(pallets: List[Pallet]) -> List[Tuple]:
    """Width-only descending, no grouping. Простейшая стратегия."""
    return sorted(pallets, key=lambda p: (-p.width, -p.height, -p.weight))


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class HybridV9Solver:
    """Greedy section-first packer — детерминированный, без рандома."""

    def __init__(
        self,
        occupancy: List[OccupancySectionSchema],
        new_pallets: List[NewPalletSchema],
        settings: OptimizationSettingsSchema,
    ):
        self.settings = settings
        self.time_limit = float(settings.timeLimitSeconds)

        sections, addresses, existing_pallets = build_warehouse_state(occupancy)
        self.sections: List[Section] = sections

        self._sec_addresses: Dict[str, List[str]] = {}
        for row in occupancy:
            self._sec_addresses[row.section_id] = [
                row.address1, row.address2, row.address3,
            ]

        self.all_pallets: List[Pallet] = [
            Pallet(
                id=p.id,
                type_size=PalletTypeSize(
                    width=p.width, height=p.height, depth=p.depth, weight=p.weight,
                ),
            )
            for p in new_pallets
        ]
        self.pallet_map: Dict[str, Pallet] = {p.id: p for p in self.all_pallets}
        self.new_pallets: List[Pallet] = list(self.all_pallets)

        self.existing_ids: set = {p.id for p in existing_pallets}
        self.existing_pallet_map: Dict[str, Pallet] = {p.id: p for p in existing_pallets}
        for p in existing_pallets:
            self.pallet_map[p.id] = p
        self.movable_existing_ids: set = {p.id for p in existing_pallets if p.movable}
        self.non_movable_ids: set = {p.id for p in existing_pallets if not p.movable}
        self.moved_existing_ids: set = set()
        self._old_address: Dict[str, str] = {
            p.id: p.current_address_id for p in existing_pallets if p.current_address_id
        }
        total_existing = len(existing_pallets)
        self._max_reslot_moves = (
            int(total_existing * settings.maxReslotPercent / 100)
            if settings.allowReslot and total_existing > 0
            else 0
        )

        self.section_states: Dict[str, _SectionState] = {}
        self._init_section_states(existing_pallets)

        self.placements: Dict[str, str] = {}
        self.compatible: Dict[str, List[str]] = {}
        self._precompute_compatibility()

    # ------------------------------------------------------------------
    # Инициализация
    # ------------------------------------------------------------------

    def _init_section_states(self, existing: List[Pallet]):
        by_section: Dict[str, List[Pallet]] = defaultdict(list)
        for p in existing:
            if p.current_section_id:
                by_section[p.current_section_id].append(p)

        for sec in self.sections:
            in_sec = by_section.get(sec.id, [])
            used = sum(p.width + sec.gap_width for p in in_sec)
            self.section_states[sec.id] = _SectionState(
                section=sec,
                free_width=sec.width - used - sec.gap_width,
                free_count=sec.max_pallets - len(in_sec),
                placed_pallets=list(in_sec),
            )

    def _precompute_compatibility(self):
        for p in self.all_pallets:
            comp = []
            for state in self.section_states.values():
                if self._basic_fits(p, state.section):
                    comp.append(state.id)
            self.compatible[p.id] = comp
        for p_id in self.movable_existing_ids:
            if p_id not in self.compatible:
                p = self.existing_pallet_map[p_id]
                comp = []
                for state in self.section_states.values():
                    if self._basic_fits(p, state.section):
                        comp.append(state.id)
                self.compatible[p_id] = comp

    @staticmethod
    def _basic_fits(pallet: Pallet, section: Section) -> bool:
        if pallet.height > section.height:
            return False
        if pallet.depth > section.depth:
            return False
        if pallet.width > section.eff_max_width:
            return False
        if section.eff_max_depth > 0 and pallet.depth > section.eff_max_depth:
            return False
        if pallet.weight > section.max_weight:
            return False
        if pallet.weight > section.max_lift_weight:
            return False
        return True

    # ------------------------------------------------------------------
    # Главный метод
    # ------------------------------------------------------------------

    def solve(self) -> OptimizationResponse:
        t0 = time.time()
        total = len(self.all_pallets)
        budget = self.time_limit
        logger.info(f"Hybrid V9 (greedy): {total} паллет × {len(self.sections)} секций, budget={budget}s")

        # Фаза 1: Multi-start BFD — пробуем 3 стратегии, берём лучшую
        best_placements = {}
        best_new_pallets = list(self.all_pallets)
        best_section_states = None

        strategies = [
            ("v3-default", _bfd_sort_strategy_1),
        ]

        for strat_name, strat_fn in strategies:
            self._reset_state()
            n = self._phase_bfd(strat_fn(self.new_pallets))
            if n > len(best_placements):
                logger.info(f"  [BFD-{strat_name}] {n}/{total}")
                best_placements = dict(self.placements)
                best_new_pallets = list(self.new_pallets)
                best_section_states = self._snapshot_states()

        self._restore_from(best_placements, best_new_pallets, best_section_states)
        n1 = len(self.placements)
        logger.info(f"  [BFD-best]    {n1}/{total} ({n1/total*100:.1f}%)")
        print(f"  [BFD-best]    {n1}/{total}, leftovers={len(self.new_pallets)}")

        # Фаза 2: Chain-swap
        n2 = self._phase_chain_swap()
        logger.info(f"  [Chain-Swap]  {n2}/{total} ({n2/total*100:.1f}%)")
        print(f"  [Chain-Swap]  {n2}/{total}, leftovers={len(self.new_pallets)}")

        # Фаза 2.5: Reslot через подбор пар/троек
        if self.new_pallets and self._max_reslot_moves > 0:
            n25 = self._phase_reslot_pairs()
            logger.info(f"  [ReslotPairs] {n25}/{total} ({n25/total*100:.1f}%)")
            print(f"  [ReslotPairs] {n25}/{total}, leftovers={len(self.new_pallets)}, moved={len(self.moved_existing_ids)}")

        # Фаза 3: Greedy fill для остатков
        if self.new_pallets:
            n3 = self._phase_greedy_fill()
            logger.info(f"  [GreedyFill]  {n3}/{total} ({n3/total*100:.1f}%)")
            print(f"  [GreedyFill]  {n3}/{total}, leftovers={len(self.new_pallets)}")

        # Фаза 4: Chain-swap 2
        if self.new_pallets:
            n4 = self._phase_chain_swap()
            logger.info(f"  [ChainSwap2]  {n4}/{total} ({n4/total*100:.1f}%)")
            print(f"  [ChainSwap2]  {n4}/{total}, leftovers={len(self.new_pallets)}")

        # Фаза 5: Адреса
        operations, not_placed = self._assign_addresses()

        elapsed = time.time() - t0
        placed_count = len([op for op in operations if op.operation == "PUT"])
        moved_count = len([op for op in operations if op.operation == "MOVE"])
        logger.info(f"Hybrid V9: итог {placed_count} placed + {moved_count} moved за {elapsed:.1f}с")
        return self._build_response(operations, not_placed, elapsed, placed_count, moved_count)

    # ------------------------------------------------------------------
    # Сброс / снапшот
    # ------------------------------------------------------------------

    def _reset_state(self):
        self.section_states.clear()
        self._init_section_states(
            [self.existing_pallet_map[pid] for pid in self.existing_ids]
        )
        self.placements.clear()
        self.new_pallets = list(self.all_pallets)
        self.moved_existing_ids.clear()

    def _snapshot_states(self) -> Dict:
        return {
            sec_id: {
                'free_width': s.free_width,
                'free_count': s.free_count,
                'placed_pallets': list(s.placed_pallets),
            }
            for sec_id, s in self.section_states.items()
        }

    def _restore_from(self, placements, new_pallets, states_snap):
        self.placements = dict(placements)
        self.new_pallets = list(new_pallets)
        if states_snap:
            for sec_id, data in states_snap.items():
                s = self.section_states[sec_id]
                s.free_width = data['free_width']
                s.free_count = data['free_count']
                s.placed_pallets = list(data['placed_pallets'])

    # ------------------------------------------------------------------
    # Фаза 1: Multi-start BFD
    # ------------------------------------------------------------------

    def _phase_bfd(self, sorted_pallets: List[Pallet]) -> int:
        for pallet in sorted_pallets:
            if pallet.id in self.placements:
                continue
            best_id = None
            min_rem = float("inf")
            for sec_id in self.compatible.get(pallet.id, []):
                state = self.section_states[sec_id]
                if state.free_count <= 0:
                    continue
                if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                    continue
                rem = state.free_width - pallet.width - state.gap_width
                if rem < min_rem:
                    min_rem = rem
                    best_id = sec_id
            if best_id:
                self._do_place(pallet.id, best_id)

        self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]
        return len(self.placements)

    # ------------------------------------------------------------------
    # Фаза 3: Greedy Section-First Fill (с резервированием гибких паллет)
    # ------------------------------------------------------------------

    def _phase_greedy_fill(self) -> int:
        """Заполняем секции с учётом резерва гибких паллет для гетерогенных пар."""
        improved = True
        iteration = 0

        while improved and self.new_pallets and iteration < 10:
            improved = False
            iteration += 1

            # Шаг 1: заполняем частично-заполненные секции
            partial = [
                sid for sid, st in self.section_states.items()
                if 0 < st.free_count < st.section.max_pallets
            ]
            if iteration == 1:
                print(f"    [Greedy] partial={len(partial)}, leftovers={len(self.new_pallets)}")

            partial.sort(key=lambda sid: (
                not self.section_states[sid].section.narrow_aisle,
                self.section_states[sid].section.height,
            ))

            for sec_id in partial:
                if self._fill_section(sec_id):
                    improved = True

            self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]
            if not self.new_pallets:
                break

            # Шаг 2: открываем секции с резервированием гибких паллет
            placed = self._fill_empty_sections_reserved()
            if placed > 0:
                improved = True

            self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]

        return len(self.placements)

    def _fill_empty_sections_reserved(self) -> int:
        """Открывает пустые секции, резервируя гибкие паллеты (W<=800) для миксов.

        Стратегия:
          1. Сначала гетерогенные пары: WIDE + FLEXIBLE
          2. Потом гомогенные пары: WIDE + WIDE (если compatible)
          3. Потом триплы из оставшихся FLEXIBLE
        """
        if not self.new_pallets:
            return 0

        # Категоризируем оставшиеся паллеты
        flexible = [p for p in self.new_pallets if p.width <= 800]
        wide = [p for p in self.new_pallets if p.width > 800]

        if not wide and not flexible:
            return 0

        placed_count = 0

        # --- Стратегия 1: гетерогенные пары WIDE + FLEXIBLE ---
        # Для каждого wide паллета ищем empty секцию + flexible партнёра
        wide_remaining = list(wide)
        flexible_remaining = list(flexible)

        for wp in sorted(wide_remaining, key=lambda p: -p.width):
            if wp.id in self.placements:
                continue
            # Лимит: проверяем только первые 30 flexible партнёров
            best_sec = self._find_best_empty_section(wp)
            if not best_sec:
                continue
            state = self.section_states[best_sec]
            best_partner = None
            best_waste = float("inf")
            for fp in flexible_remaining[:30]:
                if fp.id in self.placements:
                    continue
                if best_sec not in self.compatible.get(fp.id, []):
                    continue
                temp = [wp]
                if section_fits_pallet(state.section, temp, fp):
                    waste = (state.section.width
                             - wp.width - fp.width
                             - 3 * state.gap_width)  # 3 gaps for 2 pallets
                    if waste < best_waste:
                        best_waste = waste
                        best_partner = fp

            if best_partner:
                self._do_place(wp.id, best_sec)
                self._do_place(best_partner.id, best_sec)
                placed_count += 2
                continue

            # Без партнёра — размещаем один wide
            self._do_place(wp.id, best_sec)
            placed_count += 1

        # Обновляем списки
        self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]
        flexible_remaining = [p for p in self.new_pallets if p.width <= 800]
        wide_remaining = [p for p in self.new_pallets if p.width > 800]

        # --- Стратегия 2: гомогенные пары WIDE + WIDE ---
        # Сортируем по ширине, паруем близкие
        wide_sorted = sorted([p for p in wide_remaining if p.id not in self.placements],
                            key=lambda p: -p.width)
        i = 0
        while i < len(wide_sorted):
            p1 = wide_sorted[i]
            if p1.id in self.placements:
                i += 1
                continue
            # Ищем партнёра близкой ширины
            best_sec = self._find_best_empty_section(p1)
            if not best_sec:
                i += 1
                continue
            state = self.section_states[best_sec]
            best_j = None
            best_waste = float("inf")
            for j in range(i + 1, min(i + 20, len(wide_sorted))):
                p2 = wide_sorted[j]
                if p2.id in self.placements:
                    continue
                if best_sec not in self.compatible.get(p2.id, []):
                    continue
                temp = [p1]
                if section_fits_pallet(state.section, temp, p2):
                    waste = (state.section.width
                             - p1.width - p2.width
                             - 3 * state.gap_width)
                    if waste < best_waste:
                        best_waste = waste
                        best_j = j

            if best_j is not None:
                p2 = wide_sorted[best_j]
                self._do_place(p1.id, best_sec)
                self._do_place(p2.id, best_sec)
                placed_count += 2
            else:
                # Не нашли пару — размещаем один
                self._do_place(p1.id, best_sec)
                placed_count += 1
            i += 1

        # Обновляем
        self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]
        flexible_remaining = [p for p in self.new_pallets if p.width <= 800]

        # --- Стратегия 3: триплы из оставшихся FLEXIBLE ---
        if len(flexible_remaining) >= 3:
            flex_sorted = sorted([p for p in flexible_remaining if p.id not in self.placements],
                                key=lambda p: -p.width)
            i = 0
            while i < len(flex_sorted) - 2:
                p1 = flex_sorted[i]
                if p1.id in self.placements:
                    i += 1
                    continue
                best_sec = self._find_best_empty_section(p1)
                if not best_sec:
                    i += 1
                    continue
                state = self.section_states[best_sec]

                # Ищем ещё двух для трипла
                found = [p1]
                for j in range(i + 1, len(flex_sorted)):
                    pj = flex_sorted[j]
                    if pj.id in self.placements:
                        continue
                    if best_sec not in self.compatible.get(pj.id, []):
                        continue
                    temp = list(found)
                    if section_fits_pallet(state.section, temp, pj):
                        found.append(pj)
                        if len(found) == 3:
                            break

                if len(found) == 3:
                    for p in found:
                        self._do_place(p.id, best_sec)
                    placed_count += 3
                else:
                    # Не нашли тройку — размещаем сколько есть
                    for p in found:
                        self._do_place(p.id, best_sec)
                    placed_count += len(found)
                i += 1

        self.new_pallets = [p for p in self.all_pallets if p.id not in self.placements]
        return placed_count

    def _fill_section(self, sec_id: str) -> bool:
        """Пытается оптимально дозаполнить секцию из имеющихся паллет."""
        state = self.section_states[sec_id]
        if state.free_count <= 0:
            return False

        available = [p for p in self.new_pallets if sec_id in self.compatible.get(p.id, [])]
        if not available:
            return False

        placed_any = False
        # Перебираем комбинации: пытаемся разместить 1, 2 или 3 паллеты
        for count in range(state.free_count, 0, -1):
            best_combo = None
            best_util = -1.0  # utilisation = сумма ширин / доступная ширина

            # Для count=1: простой перебор
            if count == 1:
                for p in available:
                    if p.id in self.placements:
                        continue
                    if section_fits_pallet(state.section, state.placed_pallets, p):
                        util = p.width / state.free_width if state.free_width > 0 else 1.0
                        if util > best_util:
                            best_util = util
                            best_combo = [p]

            # Для count=2: перебор пар
            elif count == 2 and len(available) >= 2:
                for i, p1 in enumerate(available):
                    if p1.id in self.placements:
                        continue
                    for p2 in available[i+1:]:
                        if p2.id in self.placements:
                            continue
                        if p1.id == p2.id:
                            continue
                        # Пробуем разместить p1 затем p2
                        temp = list(state.placed_pallets)
                        if section_fits_pallet(state.section, temp, p1):
                            temp.append(p1)
                            if section_fits_pallet(state.section, temp, p2):
                                util = (p1.width + p2.width) / state.free_width if state.free_width > 0 else 1.0
                                if util > best_util:
                                    best_util = util
                                    best_combo = [p1, p2]

            # Для count=3: перебор троек (ограниченно)
            elif count == 3 and len(available) >= 3:
                for i, p1 in enumerate(available):
                    if p1.id in self.placements:
                        continue
                    for j, p2 in enumerate(available[i+1:], i+1):
                        if p2.id in self.placements:
                            continue
                        temp = list(state.placed_pallets)
                        if not section_fits_pallet(state.section, temp, p1):
                            continue
                        temp.append(p1)
                        if not section_fits_pallet(state.section, temp, p2):
                            continue
                        temp.append(p2)
                        for p3 in available[j+1:]:
                            if p3.id in self.placements:
                                continue
                            if p1.id == p3.id or p2.id == p3.id:
                                continue
                            if section_fits_pallet(state.section, temp, p3):
                                util = (p1.width + p2.width + p3.width) / state.free_width if state.free_width > 0 else 1.0
                                if util > best_util:
                                    best_util = util
                                    best_combo = [p1, p2, p3]

            if best_combo:
                for p in best_combo:
                    self._do_place(p.id, sec_id)
                placed_any = True
                break  # Разместили лучшую комбинацию для этого count

        return placed_any

    def _pick_next_pallet(self) -> Optional[Pallet]:
        """Выбирает следующий паллет из очереди: most-constrained-first."""
        if not self.new_pallets:
            return None
        # Приоритет: самый constrained (минимум совместимых секций), затем широкий, тяжёлый
        return min(self.new_pallets, key=lambda p: (
            len(self.compatible.get(p.id, [])),
            -p.width,
            -p.weight,
        ))

    def _find_best_empty_section(self, pallet: Pallet) -> Optional[str]:
        """Находит лучшую пустую секцию для паллета."""
        compatible = self.compatible.get(pallet.id, [])
        best_id = None
        best_score = (999, 999, 999999.0)

        for sec_id in compatible[:50]:
            state = self.section_states[sec_id]
            if state.free_count <= 0:
                continue
            if not section_fits_pallet(state.section, state.placed_pallets, pallet):
                continue

            # Score: предпочитаем секции с близкой высотой (не тратить высокие на низкие)
            # и секции где после размещения останется место
            height_waste = state.section.height - pallet.height
            remaining_free = state.free_count - 1
            # Секции где после размещения можно ещё что-то добавить — приоритет
            can_fill_more = 0 if remaining_free > 0 else 1
            # Narrow aisle match
            narrow_match = 0 if (pallet.is_narrow == state.section.narrow_aisle) else 1

            score = (can_fill_more, narrow_match, height_waste)
            if score < best_score:
                best_score = score
                best_id = sec_id

        return best_id

    # ------------------------------------------------------------------
    # Chain-Swap (depth-1, с поддержкой реслота)
    # ------------------------------------------------------------------

    def _phase_chain_swap(self) -> int:
        if not self.new_pallets:
            return len(self.placements)

        improved = True
        iteration = 0
        while improved and iteration < 5:
            improved = False
            iteration += 1
            leftovers = sorted(self.new_pallets, key=lambda p: (-p.width, -p.height))
            self.new_pallets = []

            for leftover in leftovers:
                chain = self._find_chain(leftover)
                if chain:
                    self._execute_chain(chain)
                    improved = True
                else:
                    self.new_pallets.append(leftover)

        return len(self.placements)

    def _find_chain(self, leftover: Pallet) -> Optional[List[Tuple[str, str, str]]]:
        compatible = self.compatible.get(leftover.id, [])
        if not compatible:
            return None

        # Уровень 0: прямое размещение
        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count > 0 and section_fits_pallet(
                state_a.section, state_a.placed_pallets, leftover,
            ):
                return [("place", leftover.id, sec_a_id)]

        # Уровень 1: переместить одну паллету
        for sec_a_id in compatible:
            state_a = self.section_states[sec_a_id]
            if state_a.free_count <= 0:
                continue
            gap_a = state_a.gap_width
            req = leftover.width + gap_a

            for placed_a in list(state_a.placed_pallets):
                if placed_a.id in self.non_movable_ids:
                    continue
                if placed_a.id in self.movable_existing_ids:
                    if len(self.moved_existing_ids) >= self._max_reslot_moves:
                        continue
                if state_a.free_width + placed_a.width + gap_a < req:
                    continue

                for sec_b_id in self.compatible.get(placed_a.id, []):
                    if sec_b_id == sec_a_id:
                        continue
                    state_b = self.section_states[sec_b_id]
                    if state_b.free_count > 0 and section_fits_pallet(
                        state_b.section, state_b.placed_pallets, placed_a,
                    ):
                        return [
                            ("remove", placed_a.id, sec_a_id),
                            ("place", leftover.id, sec_a_id),
                            ("place", placed_a.id, sec_b_id),
                        ]

        return None

    def _execute_chain(self, chain: List[Tuple[str, str, str]]):
        for action, p_id, sec_id in chain:
            if action == "remove":
                self._do_remove(p_id, sec_id)
        for action, p_id, sec_id in chain:
            if action == "place":
                self._do_place(p_id, sec_id)
                if p_id in self.movable_existing_ids:
                    self.moved_existing_ids.add(p_id)

    # ------------------------------------------------------------------
    # Операции с состоянием
    # ------------------------------------------------------------------

    def _do_place(self, pallet_id: str, section_id: str):
        state = self.section_states[section_id]
        pallet = self.pallet_map[pallet_id]
        gap = state.gap_width
        state.free_width -= (pallet.width + gap)
        state.free_count -= 1
        state.placed_pallets.append(pallet)
        self.placements[pallet_id] = section_id

    def _do_remove(self, pallet_id: str, section_id: str):
        state = self.section_states[section_id]
        pallet = self.pallet_map.get(pallet_id) or self.existing_pallet_map.get(pallet_id)
        if pallet is None:
            return
        gap = state.gap_width
        state.free_width += pallet.width + gap
        state.free_count += 1
        state.placed_pallets = [p for p in state.placed_pallets if p.id != pallet_id]
        self.placements.pop(pallet_id, None)

    # ------------------------------------------------------------------
    # Reslot через подбор пар (2 leftovers → 1 секция, ценой 1 MOVE)
    # ------------------------------------------------------------------

    def _phase_reslot_pairs(self) -> int:
        """Реслот: ломаем триплы чтобы достать гибкие паллеты для микса с wide leftover."""
        if not self.new_pallets or self._max_reslot_moves <= 0:
            return len(self.placements)

        improved = True
        iteration = 0
        total_placed = 0
        while improved and iteration < 5:
            improved = False
            iteration += 1

            leftovers = sorted(self.new_pallets, key=lambda p: -p.width)
            self.new_pallets = []

            for leftover in leftovers:
                if leftover.id in self.placements:
                    continue
                placed = self._try_reslot_pair(leftover)
                if placed:
                    improved = True
                    total_placed += 1
                else:
                    self.new_pallets.append(leftover)

        if total_placed > 0:
            print(f"    [ReslotPairs] placed {total_placed} pallets via pair-matching")
        return len(self.placements)

    def _try_reslot_pair(self, leftover: Pallet) -> bool:
        """Пробует разместить leftover с flexible партнёром, вытеснив 1 existing
        или забрав flexible из трипла."""
        compat = self.compatible.get(leftover.id, [])
        if not compat:
            return False

        # Ищем flexible партнёров: сначала из leftover'ов, потом из триплов
        flex_from_leftovers = [p for p in self.new_pallets
                              if p.id != leftover.id and p.width <= 860]

        for sec_id in compat[:40]:
            state = self.section_states[sec_id]

            # Вариант А: leftover уже влезает
            if state.free_count > 0 and section_fits_pallet(
                state.section, state.placed_pallets, leftover,
            ):
                self._do_place(leftover.id, sec_id)
                return True

            # Вариант Б: вытесняем 1 existing
            for existing in list(state.placed_pallets):
                if existing.id not in self.movable_existing_ids:
                    continue
                if len(self.moved_existing_ids) >= self._max_reslot_moves:
                    continue

                temp = [p for p in state.placed_pallets if p.id != existing.id]
                if not section_fits_pallet(state.section, temp, leftover):
                    continue

                # Б1: leftover один (без партнёра)
                new_home = self._find_new_home(existing)
                if new_home:
                    self._do_remove(existing.id, sec_id)
                    self._do_place(leftover.id, sec_id)
                    self._do_place(existing.id, new_home)
                    self.moved_existing_ids.add(existing.id)
                    return True

                # Б2: leftover + flexible партнёр
                partner = None
                partner_source = None

                for fp in flex_from_leftovers[:30]:
                    if fp.id in self.placements:
                        continue
                    if sec_id not in self.compatible.get(fp.id, []):
                        continue
                    temp2 = list(temp) + [leftover]
                    if section_fits_pallet(state.section, temp2, fp):
                        partner = fp
                        partner_source = 'leftover'
                        break

                if not partner:
                    for fp_id, fp_sec in self._find_flexible_in_full_section():
                        if sec_id not in self.compatible.get(fp_id, []):
                            continue
                        fp = self.pallet_map[fp_id]
                        temp2 = list(temp) + [leftover]
                        if section_fits_pallet(state.section, temp2, fp):
                            partner = fp
                            partner_source = ('triple', fp_sec)
                            break

                if not partner:
                    continue

                new_home = self._find_new_home(existing)
                if not new_home:
                    continue

                if partner_source and partner_source[0] == 'triple':
                    fp_sec = partner_source[1]
                    self._do_remove(partner.id, fp_sec)
                    self.moved_existing_ids.add(partner.id)
                self._do_remove(existing.id, sec_id)
                self._do_place(leftover.id, sec_id)
                self._do_place(partner.id, sec_id)
                self._do_place(existing.id, new_home)
                self.moved_existing_ids.add(existing.id)
                return True

        return False

    def _find_flexible_in_full_section(self):
        """Ищет W<=860 паллеты в заполненных секциях (триплах) которые можно забрать."""
        for sec_id, state in self.section_states.items():
            if state.free_count > 0:
                continue
            if len(state.placed_pallets) < 2:
                continue
            for p in state.placed_pallets:
                if p.width <= 860 and p.id in self.movable_existing_ids:
                    if len(self.moved_existing_ids) < self._max_reslot_moves:
                        yield (p.id, sec_id)

    def _find_new_home(self, pallet: Pallet) -> Optional[str]:
        """Ищет секцию куда можно переместить вытесненный existing паллет."""
        compat = self.compatible.get(pallet.id, [])
        for sec_id in compat:
            state = self.section_states[sec_id]
            if state.free_count > 0 and section_fits_pallet(
                state.section, state.placed_pallets, pallet,
            ):
                return sec_id
        return None

    # ------------------------------------------------------------------
    # Адреса
    # ------------------------------------------------------------------

    def _assign_addresses(self) -> Tuple[List[OperationSchema], List[NotPlacedSchema]]:
        all_ops: List[OperationSchema] = []
        by_section: Dict[str, List[str]] = defaultdict(list)
        for sec_id, state in self.section_states.items():
            for p in state.placed_pallets:
                by_section[sec_id].append(p.id)

        for sec_id, p_ids in by_section.items():
            section = self.section_states[sec_id].section
            real_addresses = self._sec_addresses.get(sec_id, ["", "", ""])

            occupied: set = set()
            occupied_widths: Dict[int, float] = {}
            for p_id in list(p_ids):
                if p_id in self.existing_ids and p_id not in self.moved_existing_ids:
                    old_addr = self._old_address.get(p_id, "")
                    if old_addr and old_addr in real_addresses:
                        idx = real_addresses.index(old_addr)
                        p = self.pallet_map.get(p_id)
                        if p:
                            occupied.add(idx)
                            occupied_widths[idx] = p.width
                            if idx in (0, 2) and p.width > section.width / 3:
                                occupied.add(1)
                                occupied_widths[1] = p.width

            sorted_ids = sorted(
                p_ids,
                key=lambda pid: (
                    0 if pid in self.moved_existing_ids else
                    1 if pid in self.existing_ids else 2,
                    -(self.pallet_map[pid].width if pid in self.pallet_map else 0),
                ),
            )

            for p_id in sorted_ids:
                if p_id in self.existing_ids and p_id not in self.moved_existing_ids:
                    continue
                p = self.pallet_map.get(p_id)
                if p is None:
                    continue
                w = p.width
                W = section.width
                if w > W * 2 / 3:
                    allowed = [2]
                elif w > W / 3:
                    allowed = [1, 3]
                else:
                    allowed = [1, 2, 3]

                assigned = None
                for pos in allowed:
                    idx = pos - 1
                    if idx == 1:
                        if 0 in occupied and occupied_widths.get(0, 0) > W / 3:
                            continue
                        if 2 in occupied and occupied_widths.get(2, 0) > W / 3:
                            continue
                    if idx not in occupied and real_addresses[idx]:
                        assigned = idx
                        break
                if assigned is None:
                    for idx in range(3):
                        if idx == 1:
                            if 0 in occupied and occupied_widths.get(0, 0) > W / 3:
                                continue
                            if 2 in occupied and occupied_widths.get(2, 0) > W / 3:
                                continue
                        if idx not in occupied and real_addresses[idx]:
                            assigned = idx
                            break

                if assigned is not None:
                    occupied.add(assigned)
                    occupied_widths[assigned] = w
                    if assigned in (0, 2) and w > W / 3:
                        occupied.add(1)
                        occupied_widths[1] = w
                    new_addr = real_addresses[assigned]
                    old_addr = self._old_address.get(p_id) if p_id in self.existing_ids else None
                    all_ops.append(OperationSchema(
                        pallet=p_id,
                        operation="MOVE" if (p_id in self.moved_existing_ids and old_addr) else "PUT",
                        oldAddress=old_addr if p_id in self.moved_existing_ids else None,
                        newAddress=new_addr,
                        sequence=len(all_ops) + 1,
                    ))
                else:
                    self.placements.pop(p_id, None)

        addressed_ids = {op.pallet for op in all_ops}
        for p_id in list(self.placements.keys()):
            if p_id not in addressed_ids:
                self.placements.pop(p_id, None)

        # MOVE перед PUT
        all_ops.sort(key=lambda op: (0 if op.operation == "MOVE" else 1, op.sequence))
        for i, op in enumerate(all_ops):
            op.sequence = i + 1

        not_placed = [
            NotPlacedSchema(pallet=p.id, reason="NO_SPACE")
            for p in self.new_pallets
        ]
        return all_ops, not_placed

    # ------------------------------------------------------------------
    # Ответ
    # ------------------------------------------------------------------

    def _build_response(
        self, operations, not_placed, elapsed, placed_count, moved_count=0,
    ) -> OptimizationResponse:
        return OptimizationResponse(
            optimizationId="hybrid-v9",
            mode="place",
            solverStatus=SolverStatus.FEASIBLE,
            placementStatus=PlacementStatus.COMPLETE if not not_placed else PlacementStatus.PARTIAL,
            score=float(placed_count * 100000),
            executionTimeSeconds=round(elapsed, 1),
            operations=operations,
            notPlaced=not_placed,
            metrics=MetricsSchema(
                placedPallets=placed_count,
                movedPallets=moved_count,
                notPlacedPallets=len(not_placed),
                potentialLoss=0,
                usedSections=len(set(op.newAddress for op in operations)),
            ),
        )


def run_hybrid_v9(req: OptimizationRequest) -> OptimizationResponse:
    solver = HybridV9Solver(
        occupancy=req.occupancy,
        new_pallets=req.newPallets,
        settings=req.settings,
    )
    return solver.solve()
