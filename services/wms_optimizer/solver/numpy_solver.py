"""NumPy+Pandas Solver — типоразмерная агрегация + VAM + дезагрегация.

Алгоритм (без OR-Tools, только NumPy):
1. Группировка паллет по типоразмерам (width, height, depth, weight)
2. Группировка секций по бакетам (одинаковая остаточная вместимость)
3. Cost-матрица с приоритетами (narrow→narrow, wide→narrow штраф)
4. Эффективный demand с учётом ширины (защита от переаллокации)
5. VAM (Vogel's Approximation Method) — тип×бакет распределение
6. Дезагрегация через section_fits_pallet
7. Leftover resolution: консолидация + виртуальный реслот

Контракт идентичен CPSATAggregatedSolver.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from models.pallet import Pallet
from models.section import Section
from optimizer.potential import section_fits_pallet

try:
    from scipy.optimize import linprog, Bounds
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

logger = logging.getLogger(__name__)

INF = 1e12

TypeKey = Tuple[float, float, float, float]
BucketKey = Tuple[float, float, float, float, float, bool, float, int, float, float]


class NumpySolver:
    """Солвер на чистом NumPy с типоразмерной агрегацией и VAM."""

    def __init__(
        self,
        sections: List[Section],
        new_pallets: List[Pallet],
        existing_pallets: List[Pallet],
        addresses,
        settings,
        warm_start: Optional[Dict[str, str]] = None,
    ):
        self.sections = sections
        self.new_pallets = new_pallets
        self.existing_pallets = existing_pallets
        self.addresses = addresses
        self.settings = settings

        # Разделяем fixed (immovable) и movable existing pallets
        self.fixed_pallets = [p for p in existing_pallets if not p.movable]
        self.movable_existing = [p for p in existing_pallets if p.movable]

        # section_pallets только с FIXED паллетами (movable будут переразмещены)
        self.section_pallets: Dict[str, List[Pallet]] = {s.id: [] for s in sections}
        fixed_map = {p.id: p for p in self.fixed_pallets}
        for addr in addresses:
            if addr.pallet_id is not None and addr.pallet_id in fixed_map:
                self.section_pallets[addr.section_id].append(fixed_map[addr.pallet_id])

        # Текущая секция каждой паллеты (для отслеживания MOVE)
        self.pallet_current_section: Dict[str, str] = {}
        for addr in addresses:
            if addr.pallet_id is not None:
                self.pallet_current_section[addr.pallet_id] = addr.section_id

        self.solver_branches = 0
        self.solver_conflicts = 0
        self.solver_wall_time = 0.0

    # ==================================================================
    # Основной метод
    # ==================================================================

    def solve(self) -> Tuple[Dict[str, Optional[str]], str, float]:
        t_start = time.perf_counter()
        strict_narrow = self.settings.strictNarrowAislePlacement

        # Инициализация assignment
        assignment: Dict[str, Optional[str]] = {
            p.id: self.pallet_current_section.get(p.id) for p in self.existing_pallets
        }
        for p in self.new_pallets:
            assignment[p.id] = None

        # Определяем какие паллеты размещать
        allow_reslot = self.settings.allowReslot
        pallets_to_place = list(self.new_pallets)
        if allow_reslot and self.movable_existing:
            pallets_to_place = list(self.new_pallets) + list(self.movable_existing)
            logger.info(
                "NumPy reslot: +%d movable existing паллет в пул размещения",
                len(self.movable_existing),
            )

        if not pallets_to_place:
            self.solver_wall_time = time.perf_counter() - t_start
            return assignment, "OPTIMAL", 0.0

        # 1. Построить бакеты секций
        bucket_keys, bucket_data = self._build_buckets()

        # 2. Сгруппировать паллеты по типоразмерам
        type_keys, type_data = self._group_pallets(pallets_to_place)

        if not bucket_data:
            self.solver_wall_time = time.perf_counter() - t_start
            return assignment, "FEASIBLE", 0.0

        n_types = len(type_keys)
        n_buckets = len(bucket_keys)
        logger.info(
            "NumPy: типов=%d бакетов=%d паллет=%d (new=%d movable=%d)",
            n_types, n_buckets, len(pallets_to_place),
            len(self.new_pallets), len(self.movable_existing),
        )

        # 3. Cost-матрица и маски допустимых пар
        cost, feasible_strict, feasible_loose = self._build_cost_and_feasible(
            type_keys, type_data, bucket_keys, bucket_data, strict_narrow,
        )

        # 4. LP/MILP solve (scipy) или fallback на type-level greedy
        allocation = np.zeros((n_types, n_buckets), dtype=int)
        if HAS_SCIPY:
            self._lp_solve(
                allocation, type_keys, type_data, bucket_keys, bucket_data,
                cost, feasible_strict, strict_narrow,
            )
        else:
            self._type_level_greedy(
                allocation, type_keys, type_data, bucket_keys, bucket_data,
                cost, feasible_strict, feasible_loose, strict_narrow,
            )
        initial_placed = int(allocation.sum())
        logger.info("NumPy LP: разместил %d/%d паллет", initial_placed, len(pallets_to_place))

        # 6. Дезагрегация
        self._disaggregate(
            allocation, type_keys, type_data, bucket_keys, bucket_data,
            assignment, strict_narrow,
        )

        # 7. Leftover resolution
        leftover = [p for p in pallets_to_place if assignment[p.id] is None]
        if leftover:
            live_state = self._build_live_state(assignment)
            before = len(leftover)
            self._resolve_leftovers(leftover, assignment, live_state, strict_narrow)
            after = len([p for p in leftover if assignment[p.id] is None])
            logger.info("NumPy+VAM leftover: +%d паллет", before - after)

        # Итог
        final_placed = sum(1 for p in pallets_to_place if assignment[p.id] is not None)
        solver_status = "OPTIMAL" if final_placed == len(pallets_to_place) else "FEASIBLE"

        narrow_bonus = 0
        wide_penalty = 0
        for p in pallets_to_place:
            sec_id = assignment.get(p.id)
            if sec_id is None:
                continue
            sec = self._section_by_id(sec_id)
            if sec is None:
                continue
            if p.is_narrow and sec.narrow_aisle:
                narrow_bonus += 1
            if not p.is_narrow and sec.narrow_aisle:
                wide_penalty += 1

        score = 100000.0 * final_placed + 10.0 * narrow_bonus - 5000.0 * wide_penalty
        self.solver_wall_time = time.perf_counter() - t_start

        logger.info(
            "NumPy: placed=%d/%d status=%s score=%.0f time=%.2fs (new=%d movable=%d)",
            final_placed, len(pallets_to_place), solver_status, score, self.solver_wall_time,
            len(self.new_pallets), len(self.movable_existing),
        )
        return assignment, solver_status, score

    # ==================================================================
    # Бакеты и типы
    # ==================================================================

    def _build_buckets(self) -> Tuple[List[BucketKey], List[dict]]:
        """Группировка секций по бакетам с одинаковой остаточной вместимостью."""
        grouped: Dict[BucketKey, List[Section]] = {}
        for sec in self.sections:
            existing = self.section_pallets.get(sec.id, [])
            cnt = len(existing)
            w_sum = sum(p.width for p in existing)
            wt_sum = sum(p.weight for p in existing)

            remaining_cnt = sec.max_pallets - cnt
            if remaining_cnt <= 0:
                continue
            remaining_w = sec.width - w_sum - (cnt + 1) * sec.gap_width
            if remaining_w <= 0:
                continue
            remaining_wt = (
                math.inf if math.isinf(sec.max_weight) else sec.max_weight - wt_sum
            )
            if remaining_wt < 0:
                continue

            key: BucketKey = (
                sec.height, sec.depth, sec.max_lift_weight,
                sec.eff_max_width, sec.eff_max_depth, sec.narrow_aisle,
                round(sec.gap_width, 3),
                remaining_cnt, round(remaining_w, 3),
                math.inf if math.isinf(remaining_wt) else round(remaining_wt, 3),
            )
            grouped.setdefault(key, []).append(sec)

        bucket_keys: List[BucketKey] = []
        bucket_data: List[dict] = []
        for key, secs in grouped.items():
            n = len(secs)
            bucket_keys.append(key)
            bucket_data.append({
                "sections": secs,
                "n_sections": n,
                "total_count": key[7] * n,
                "total_width": key[8] * n,
                "total_weight": math.inf if math.isinf(key[9]) else key[9] * n,
                "narrow_aisle": key[5],
                "height": key[0], "depth": key[1], "max_lift_weight": key[2],
                "eff_max_width": key[3], "eff_max_depth": key[4],
                "gap_width": key[6],
                "per_count": key[7], "per_width": key[8], "per_weight": key[9],
            })

        return bucket_keys, bucket_data

    @staticmethod
    def _type_key(p: Pallet) -> TypeKey:
        return (p.width, p.height, p.depth, p.weight)

    def _group_pallets(self, pallets: List[Pallet]) -> Tuple[List[TypeKey], List[dict]]:
        groups: Dict[TypeKey, List[Pallet]] = {}
        for p in pallets:
            groups.setdefault(self._type_key(p), []).append(p)

        type_keys, type_data = [], []
        for key, pallets in groups.items():
            rep = pallets[0]
            type_keys.append(key)
            type_data.append({
                "width": rep.width, "height": rep.height,
                "depth": rep.depth, "weight": rep.weight,
                "is_narrow": rep.is_narrow,
                "count": len(pallets),
                "pallets": list(pallets),
            })
        return type_keys, type_data

    # ==================================================================
    # Cost-матрица и эффективный demand
    # ==================================================================

    def _build_cost_and_feasible(
        self,
        type_keys: List[TypeKey],
        type_data: List[dict],
        bucket_keys: List[BucketKey],
        bucket_data: List[dict],
        strict_narrow: bool,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cost-матрица и ДВЕ маски: strict (narrow→narrow) и loose (narrow→any)."""
        n_types = len(type_keys)
        n_buckets = len(bucket_keys)
        cost = np.full((n_types, n_buckets), INF, dtype=np.float64)
        feasible_strict = np.zeros((n_types, n_buckets), dtype=bool)
        feasible_loose = np.zeros((n_types, n_buckets), dtype=bool)

        for ti, td in enumerate(type_data):
            w, h, d, wt = td["width"], td["height"], td["depth"], td["weight"]
            is_narrow = td["is_narrow"]

            for bi, bd in enumerate(bucket_data):
                if h > bd["height"] or d > bd["depth"] or wt > bd["max_lift_weight"]:
                    continue
                if w > bd["eff_max_width"] or d > bd["eff_max_depth"]:
                    continue

                # Loose: всегда feasible (кроме strict narrow которое проверяется отдельно)
                feasible_loose[ti, bi] = True

                # Strict: narrow паллеты только в narrow секции
                if strict_narrow and is_narrow and not bd["narrow_aisle"]:
                    # NOT feasible_strict, НО feasible_loose (уже установлен выше)
                    pass
                else:
                    feasible_strict[ti, bi] = True

                # Проверка ширины (общая)
                pallet_w = w + bd["gap_width"]
                if pallet_w > bd["per_width"] + bd["gap_width"]:
                    feasible_strict[ti, bi] = False
                    feasible_loose[ti, bi] = False
                    continue

                if is_narrow and bd["narrow_aisle"]:
                    cost[ti, bi] = -10000.0 + w
                elif not is_narrow and bd["narrow_aisle"]:
                    cost[ti, bi] = 5000.0 + w
                else:
                    cost[ti, bi] = -w

        f_strict = int(feasible_strict.sum())
        f_loose = int(feasible_loose.sum())
        logger.info("NumPy cost: %d×%d strict=%d loose=%d", n_types, n_buckets, f_strict, f_loose)
        return cost, feasible_strict, feasible_loose

    def _compute_effective_demand(
        self,
        type_data: List[dict],
        bucket_keys: List[BucketKey],
        bucket_data: List[dict],
        cost: np.ndarray,
        feasible: np.ndarray,
    ) -> np.ndarray:
        """Эффективный demand: min(слоты, вместимость_по_ширине).

        Ключевая защита от переаллокации: VAM распределяет по слотам,
        но слот может быть 1×2000мм — в него влезет одна паллета 900мм
        или две по 600мм. Без ограничения по ширине VAM может нараспределять
        больше чем физически влезет.
        """
        n_buckets = len(bucket_data)
        demand = np.zeros(n_buckets, dtype=np.int32)

        for bi, bd in enumerate(bucket_data):
            slots = bd["total_count"]
            if slots <= 0:
                continue

            # Минимальная ширина совместимой паллеты
            compatible_ti = np.flatnonzero(feasible[:, bi])
            if len(compatible_ti) == 0:
                continue

            min_w = min(type_data[ti]["width"] for ti in compatible_ti)
            gap = bd["gap_width"]

            # Сколько ТАКИХ паллет влезет в одну секцию по ширине?
            per_sec_width = bd["per_width"]
            # per_width = section.width - occupied_width - (occupied+1)*gap
            # После размещения паллеты: used_width += pallet_width + gap
            # remaining после: per_width - pallet_width - gap
            # Максимум паллет: пока per_width >= pallet_width + gap
            max_per_sec = 1
            remaining = per_sec_width
            while remaining >= min_w + gap and max_per_sec < bd["per_count"]:
                remaining -= (min_w + gap)
                max_per_sec += 1

            effective = min(slots, max_per_sec * bd["n_sections"])
            demand[bi] = effective

        total_demand = int(demand.sum())
        total_slots = sum(bd["total_count"] for bd in bucket_data)
        logger.info(
            "NumPy+VAM demand: slots=%d effective=%d (%.0f%%)",
            total_slots, total_demand, 100 * total_demand / max(1, total_slots),
        )
        return demand

    # ==================================================================
    # VAM (Vogel's Approximation Method)
    # ==================================================================

    def _vam_solve(
        self, supply: np.ndarray, demand: np.ndarray, cost: np.ndarray,
    ) -> np.ndarray:
        """Метод аппроксимации Фогеля."""
        n_s, n_d = cost.shape
        supply = supply.copy()
        demand = demand.copy()
        alloc = np.zeros((n_s, n_d), dtype=int)

        while True:
            active_r = supply > 0
            active_c = demand > 0
            if not active_r.any() or not active_c.any():
                break

            row_pen = np.full(n_s, -1.0)
            for i in np.flatnonzero(active_r):
                rc = cost[i, active_c]
                if len(rc) == 0:
                    continue
                row_pen[i] = INF if len(rc) == 1 else np.partition(rc, 1)[1] - rc.min()

            col_pen = np.full(n_d, -1.0)
            for j in np.flatnonzero(active_c):
                cc = cost[active_r, j]
                if len(cc) == 0:
                    continue
                col_pen[j] = INF if len(cc) == 1 else np.partition(cc, 1)[1] - cc.min()

            max_rp, max_cp = row_pen.max(), col_pen.max()
            if max_rp <= 0 and max_cp <= 0:
                break

            if max_rp >= max_cp:
                i = int(row_pen.argmax())
                aj = np.flatnonzero(active_c)
                j = int(aj[cost[i, aj].argmin()])
            else:
                j = int(col_pen.argmax())
                ai = np.flatnonzero(active_r)
                i = int(ai[cost[ai, j].argmin()])

            amt = min(int(supply[i]), int(demand[j]))
            if amt <= 0:
                supply[i] = 0
                continue
            alloc[i, j] = amt
            supply[i] -= amt
            demand[j] -= amt

        return alloc

    # ==================================================================
    # LP/MILP solver (scipy)
    # ==================================================================

    def _lp_solve(
        self,
        allocation: np.ndarray,
        type_keys: List[TypeKey],
        type_data: List[dict],
        bucket_keys: List[BucketKey],
        bucket_data: List[dict],
        cost: np.ndarray,
        feasible: np.ndarray,
        strict_narrow: bool,
    ) -> None:
        """Solve type-level allocation with scipy LP relaxation + rounding.

        LP relaxation → round down → greedy fill remainder.
        Fast (simplex, seconds) and near-optimal.
        """
        n_types = len(type_keys)
        n_buckets = len(bucket_keys)

        # Build variable list: only feasible pairs
        var_list = []
        var_idx = {}
        for ti in range(n_types):
            for bi in range(n_buckets):
                if feasible[ti, bi]:
                    var_idx[(ti, bi)] = len(var_list)
                    var_list.append((ti, bi))

        n_vars = len(var_list)
        if n_vars == 0:
            return

        # --- Objective (negate for minimization) ---
        c = np.zeros(n_vars)
        for idx, (ti, bi) in enumerate(var_list):
            td = type_data[ti]
            obj = -100000.0
            if td["is_narrow"] and bucket_data[bi]["narrow_aisle"]:
                obj -= 10.0
            if not td["is_narrow"] and bucket_data[bi]["narrow_aisle"]:
                obj += 5000.0
            c[idx] = obj

        # --- Bounds ---
        supply = np.array([td["count"] for td in type_data], dtype=np.float64)
        demand_count = np.array([bd["total_count"] for bd in bucket_data], dtype=np.float64)
        ub = np.array([
            min(supply[ti], demand_count[bi], bucket_data[bi]["total_count"])
            for ti, bi in var_list
        ])
        bounds = Bounds(np.zeros(n_vars), ub)

        # --- Constraints: A_ub @ x <= b_ub ---
        # 1. Type supply: sum(y[ti,:]) <= supply[ti]
        # 2. Bucket count: sum(y[:,bi]) <= demand[bi]
        # 3. Bucket width: sum((w+gap)*y) <= total_width + total_count*gap
        # 4. Bucket weight: sum(wt*y) <= total_weight
        rows = []
        b_ub = []

        # 1. Type supply
        for ti in range(n_types):
            row = np.zeros(n_vars)
            for bi in range(n_buckets):
                idx = var_idx.get((ti, bi))
                if idx is not None:
                    row[idx] = 1.0
            rows.append(row)
            b_ub.append(supply[ti])

        # 2. Bucket count
        for bi in range(n_buckets):
            row = np.zeros(n_vars)
            for ti in range(n_types):
                idx = var_idx.get((ti, bi))
                if idx is not None:
                    row[idx] = 1.0
            rows.append(row)
            b_ub.append(demand_count[bi])

        # 3. Bucket width
        for bi in range(n_buckets):
            bd = bucket_data[bi]
            gap = bd["gap_width"]
            row = np.zeros(n_vars)
            for ti in range(n_types):
                idx = var_idx.get((ti, bi))
                if idx is not None:
                    row[idx] = type_data[ti]["width"] + gap
            rows.append(row)
            b_ub.append(bd["total_width"] + bd["total_count"] * gap)

        # 4. Bucket weight
        for bi in range(n_buckets):
            bd = bucket_data[bi]
            if math.isinf(bd["total_weight"]):
                continue  # Skip unconstrained
            row = np.zeros(n_vars)
            for ti in range(n_types):
                idx = var_idx.get((ti, bi))
                if idx is not None:
                    row[idx] = type_data[ti]["weight"]
            rows.append(row)
            b_ub.append(bd["total_weight"])

        if not rows:
            return

        A_ub = np.array(rows)
        b_ub = np.array(b_ub)

        # --- Solve LP (continuous relaxation) ---
        logger.info("NumPy LP: vars=%d constraints=%d", n_vars, len(b_ub))
        try:
            res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        except Exception as e:
            logger.warning("NumPy LP error: %s — fallback to greedy", e)
            self._type_level_greedy(
                allocation, type_keys, type_data, bucket_keys, bucket_data,
                cost, feasible, feasible, strict_narrow,
            )
            return

        if not res.success:
            logger.warning("NumPy LP failed: %s — fallback to greedy", res.message)
            self._type_level_greedy(
                allocation, type_keys, type_data, bucket_keys, bucket_data,
                cost, feasible, feasible, strict_narrow,
            )
            return

        # --- Round down ---
        for idx, (ti, bi) in enumerate(var_list):
            val = int(res.x[idx])  # floor
            if val > 0:
                allocation[ti, bi] = val

        lp_placed = int(allocation.sum())
        logger.info("NumPy LP: continuous=%.0f rounded=%d", -res.fun / 100000, lp_placed)

        # --- Greedy fill remainder ---
        # Recompute remaining capacity
        rem_count = np.array([bd["total_count"] for bd in bucket_data], dtype=np.float64)
        rem_width = np.array([bd["total_width"] for bd in bucket_data], dtype=np.float64)
        for idx, (ti, bi) in enumerate(var_list):
            val = allocation[ti, bi]
            if val > 0:
                rem_count[bi] -= val
                gap = bucket_data[bi]["gap_width"]
                rem_width[bi] -= val * (type_data[ti]["width"] + gap)

        # Greedy fill: iterate types in priority order
        type_order = sorted(
            range(n_types),
            key=lambda ti: (not type_data[ti]["is_narrow"], -type_data[ti]["height"], -type_data[ti]["width"]),
        )

        for ti in type_order:
            remaining = type_data[ti]["count"] - int(allocation[ti].sum())
            if remaining <= 0:
                continue
            td = type_data[ti]
            w = td["width"]

            candidates = sorted(
                [bi for bi in range(n_buckets) if feasible[ti, bi] and rem_count[bi] > 0],
                key=lambda bi: cost[ti, bi],
            )
            for bi in candidates:
                if remaining <= 0:
                    break
                gap = bucket_data[bi]["gap_width"]
                max_by_w = int(rem_width[bi] // (w + gap)) if (w + gap) > 0 else 0
                add = min(remaining, int(rem_count[bi]), max_by_w)
                if add > 0:
                    allocation[ti, bi] += add
                    remaining -= add
                    rem_count[bi] -= add
                    rem_width[bi] -= add * (w + gap)

        total = int(allocation.sum())
        logger.info("NumPy LP+greedy: total=%d (+%d greedy)", total, total - lp_placed)

    def _type_level_greedy(
        self,
        allocation: np.ndarray,
        type_keys: List[TypeKey],
        type_data: List[dict],
        bucket_keys: List[BucketKey],
        bucket_data: List[dict],
        cost: np.ndarray,
        feasible_strict: np.ndarray,
        feasible_loose: np.ndarray,
        strict_narrow: bool,
    ) -> None:
        """Жадное распределение на уровне ТИПОВ паллет.

        Проходы:
        1-3: narrow→narrow (strict mask)
        4:   narrow→wide (LOOSE mask — fallback!)
        5:   wide→wide (strict mask)
        6:   wide→narrow (strict mask, leftover only)
        7+:  iterative refinement (loose mask, все бакеты)
        """
        n_types = len(type_keys)
        n_buckets = len(bucket_keys)

        rem_count = np.array([bd["total_count"] for bd in bucket_data], dtype=np.int32)
        rem_width = np.array([bd["total_width"] for bd in bucket_data], dtype=np.float64)

        type_order = sorted(
            range(n_types),
            key=lambda ti: (
                not type_data[ti]["is_narrow"],  # narrow first
                -type_data[ti]["height"],          # tall first
                -type_data[ti]["width"],           # wide first
            ),
        )

        narrow_bi = [bi for bi in range(n_buckets) if bucket_data[bi]["narrow_aisle"]]
        wide_bi = [bi for bi in range(n_buckets) if not bucket_data[bi]["narrow_aisle"]]
        all_bi = list(range(n_buckets))

        # --- Проход 1: narrow→narrow (strict) ---
        for ti in type_order:
            if not type_data[ti]["is_narrow"]:
                continue
            self._allocate_type(ti, type_data, bucket_data, feasible_strict, cost,
                              rem_count, rem_width, allocation, narrow_bi)

        # --- Проход 2: wide→wide (strict) ---
        for ti in type_order:
            if type_data[ti]["is_narrow"]:
                continue
            self._allocate_type(ti, type_data, bucket_data, feasible_strict, cost,
                              rem_count, rem_width, allocation, wide_bi)

        # --- Проход 3: narrow→narrow refill ---
        for ti in type_order:
            if not type_data[ti]["is_narrow"]:
                continue
            self._allocate_type(ti, type_data, bucket_data, feasible_strict, cost,
                              rem_count, rem_width, allocation, narrow_bi)

        # --- Проход 4: narrow→wide (LOOSE! fallback для узких паллет) ---
        for ti in type_order:
            if not type_data[ti]["is_narrow"]:
                continue
            self._allocate_type(ti, type_data, bucket_data, feasible_loose, cost,
                              rem_count, rem_width, allocation, wide_bi)

        # --- Проход 5: wide→wide refill ---
        for ti in type_order:
            if type_data[ti]["is_narrow"]:
                continue
            self._allocate_type(ti, type_data, bucket_data, feasible_strict, cost,
                              rem_count, rem_width, allocation, wide_bi)

        # --- Проход 6: wide→narrow (leftover only) ---
        for ti in type_order:
            if type_data[ti]["is_narrow"]:
                continue
            self._allocate_type(ti, type_data, bucket_data, feasible_strict, cost,
                              rem_count, rem_width, allocation, narrow_bi)

        # --- Iterative refinement: loose mask, all buckets ---
        for refine_pass in range(3):
            placed_before = int(allocation.sum())
            for ti in type_order:
                remaining = type_data[ti]["count"] - int(allocation[ti].sum())
                if remaining <= 0:
                    continue
                self._allocate_type(ti, type_data, bucket_data, feasible_loose, cost,
                                  rem_count, rem_width, allocation, all_bi)
            if int(allocation.sum()) == placed_before:
                break

    def _allocate_type(
        self,
        ti: int,
        type_data: List[dict],
        bucket_data: List[dict],
        feasible: np.ndarray,
        cost: np.ndarray,
        rem_count: np.ndarray,
        rem_width: np.ndarray,
        allocation: np.ndarray,
        preferred_buckets: List[int],
    ) -> None:
        """Разместить максимум паллет типа ti в указанные бакеты.

        Использует best-fit по cost: сначала бакеты с минимальным cost
        (наиболее подходящие), затем с большим cost.
        """
        td = type_data[ti]
        remaining_pallets = td["count"] - int(allocation[ti].sum())
        if remaining_pallets <= 0:
            return

        w, gap_for_type = td["width"], 0.0

        # Фильтруем совместимые бакеты из preferred
        candidates = [
            bi for bi in preferred_buckets
            if feasible[ti, bi] and rem_count[bi] > 0
        ]
        if not candidates:
            return

        # Сортируем по cost (лучшие first) + по убыванию занятости (best-fit)
        candidates.sort(key=lambda bi: (cost[ti, bi], -rem_count[bi]))

        for bi in candidates:
            if remaining_pallets <= 0:
                break
            if rem_count[bi] <= 0:
                continue

            bd = bucket_data[bi]
            gap = bd["gap_width"]

            # Сколько ещё влезет по ширине?
            pallet_w_with_gap = w + gap
            max_by_width = int(rem_width[bi] // pallet_w_with_gap) if pallet_w_with_gap > 0 else 0
            can_add = min(remaining_pallets, int(rem_count[bi]), max_by_width)
            if can_add <= 0:
                continue

            allocation[ti, bi] += can_add
            remaining_pallets -= can_add
            rem_count[bi] -= can_add
            rem_width[bi] -= can_add * pallet_w_with_gap

    # ==================================================================
    # Дезагрегация
    # ==================================================================

    def _disaggregate(
        self,
        allocation: np.ndarray,
        type_keys: List[TypeKey],
        type_data: List[dict],
        bucket_keys: List[BucketKey],
        bucket_data: List[dict],
        assignment: Dict[str, Optional[str]],
        strict_narrow: bool,
    ) -> None:
        """Разложить тип×бакет → конкретные паллеты в конкретные секции."""
        # Оставшиеся паллеты каждого типа
        remaining = {ti: list(td["pallets"]) for ti, td in enumerate(type_data)}

        # Живое состояние секций
        live_state: Dict[str, List[Pallet]] = {
            sec.id: list(self.section_pallets.get(sec.id, [])) for sec in self.sections
        }

        # Собираем паллеты по бакетам
        by_bucket: Dict[int, List[Pallet]] = {}
        for ti in range(len(type_data)):
            for bi in range(len(bucket_data)):
                n = int(allocation[ti, bi])
                if n <= 0:
                    continue
                pool = remaining[ti]
                by_bucket.setdefault(bi, []).extend(
                    [pool.pop() for _ in range(min(n, len(pool)))]
                )

        mismatch_leftover: List[Pallet] = []

        for bi, pallets_for_bucket in by_bucket.items():
            bucket_secs = bucket_data[bi]["sections"]
            leftover_this = []

            for candidate in sorted(pallets_for_bucket, key=lambda p: -p.width):
                best_sec, best_occ = None, -1
                for sec in bucket_secs:
                    if section_fits_pallet(sec, live_state[sec.id], candidate, strict_narrow):
                        occ = len(live_state[sec.id])
                        if occ > best_occ:
                            best_occ, best_sec = occ, sec
                if best_sec:
                    live_state[best_sec.id].append(candidate)
                    assignment[candidate.id] = best_sec.id
                else:
                    leftover_this.append(candidate)
            mismatch_leftover.extend(leftover_this)

        # Fallback: невлезшие в родной бакет → весь склад
        if mismatch_leftover:
            secs_sorted = sorted(self.sections, key=lambda s: (not s.narrow_aisle, -s.width))
            for pallet in sorted(mismatch_leftover, key=lambda p: (not p.is_narrow, -p.width)):
                best_sec, best_occ = None, -1
                for sec in secs_sorted:
                    if section_fits_pallet(sec, live_state[sec.id], pallet, strict_narrow):
                        occ = len(live_state[sec.id])
                        if occ > best_occ:
                            best_occ, best_sec = occ, sec
                if best_sec:
                    live_state[best_sec.id].append(pallet)
                    assignment[pallet.id] = best_sec.id

        # Never-selected типы
        never_selected = [p for pool in remaining.values() for p in pool]
        if never_selected:
            for p in sorted(never_selected, key=lambda pp: (not pp.is_narrow, -pp.width)):
                if assignment[p.id] is not None:
                    continue
                for sec in sorted(self.sections, key=lambda s: (not s.narrow_aisle, -s.width)):
                    if section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                        live_state[sec.id].append(p)
                        assignment[p.id] = sec.id
                        break

    # ==================================================================
    # Leftover resolution
    # ==================================================================

    def _build_live_state(self, assignment: Dict[str, Optional[str]]) -> Dict[str, List[Pallet]]:
        live_state: Dict[str, List[Pallet]] = {
            sec.id: list(self.section_pallets.get(sec.id, [])) for sec in self.sections
        }
        # Все размещённые паллеты (new + movable existing)
        all_placed = [p for p in self.new_pallets + self.movable_existing
                      if assignment.get(p.id)]
        for p in all_placed:
            sec_id = assignment.get(p.id)
            if sec_id:
                live_state[sec_id].append(p)
        return live_state

    def _resolve_leftovers(
        self,
        leftover: List[Pallet],
        assignment: Dict[str, Optional[str]],
        live_state: Dict[str, List[Pallet]],
        strict_narrow: bool,
    ) -> None:
        """Консолидация + виртуальный реслот."""
        before = len([p for p in leftover if assignment[p.id] is None])

        # 1. Прямой поиск
        for p in leftover:
            if assignment.get(p.id) is not None:
                continue
            for sec in sorted(self.sections, key=lambda s: (not s.narrow_aisle, -s.width)):
                if section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                    live_state[sec.id].append(p)
                    assignment[p.id] = sec.id
                    break

        still = [p for p in leftover if assignment[p.id] is None]
        if not still:
            return

        # 2. Консолидация
        self._consolidate(still, live_state, assignment, strict_narrow)
        still = [p for p in leftover if assignment[p.id] is None]
        if not still:
            return

        # 3. Виртуальный реслот
        self._virtual_reslot(still, live_state, assignment, strict_narrow)

        after = len([p for p in leftover if assignment[p.id] is None])
        logger.info("NumPy leftover: %d -> %d", before, after)

    def _consolidate(
        self, leftover, live_state, assignment, strict_narrow,
    ) -> None:
        fixed_ids = {p.id for pallets in self.section_pallets.values() for p in pallets}
        candidates = []
        for sec in self.sections:
            occ = [p for p in live_state[sec.id] if p.id not in fixed_ids]
            if not occ:
                continue
            fixed = self.section_pallets.get(sec.id, [])
            used_w = sum(p.width for p in fixed) + sum(p.width for p in occ)
            if sec.width - used_w >= sec.width / 3.0:
                candidates.append((sec, occ))
        if not candidates:
            return

        candidates.sort(key=lambda x: len(x[1]))
        freed = []
        for sec, occ in candidates:
            moves, ok = [], True
            temp_additions: Dict[str, List[Pallet]] = {}  # отслеживаем временные добавления
            for p in occ:
                found = False
                for other in self.sections:
                    if other.id == sec.id:
                        continue
                    extra = temp_additions.get(other.id, [])
                    if section_fits_pallet(other, live_state[other.id] + extra, p, strict_narrow):
                        moves.append((p, other))
                        temp_additions.setdefault(other.id, []).append(p)
                        found = True
                        break
                if not found:
                    ok = False
                    break
            if ok and len(moves) == len(occ):
                for p, tgt in moves:
                    live_state[sec.id].remove(p)
                    live_state[tgt.id].append(p)
                    assignment[p.id] = tgt.id
                freed.append(sec.id)

        if not freed:
            return
        placed = 0
        for p in leftover:
            if assignment.get(p.id):
                continue
            for sec in self.sections:
                if sec.id not in freed:
                    continue
                if section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                    live_state[sec.id].append(p)
                    assignment[p.id] = sec.id
                    placed += 1
                    break
        logger.info("NumPy консолидация: освобождено=%d разместил=%d", len(freed), placed)

    def _virtual_reslot(
        self, leftover, live_state, assignment, strict_narrow,
    ) -> None:
        fixed_ids = {p.id for pallets in self.section_pallets.values() for p in pallets}
        near_miss: Dict[str, List[Section]] = {}
        for p in leftover:
            near = []
            for sec in self.sections:
                if p.height > sec.height or p.depth > sec.depth or p.weight > sec.max_lift_weight:
                    continue
                if p.width > sec.eff_max_width or p.depth > sec.eff_max_depth:
                    continue
                if strict_narrow and p.is_narrow and not sec.narrow_aisle:
                    continue
                if not section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                    near.append(sec)
            if near:
                near_miss[p.id] = near

        if not near_miss:
            return

        placed = 0
        for p in leftover:
            if assignment.get(p.id):
                continue
            near = near_miss.get(p.id, [])
            near.sort(key=lambda s: sum(1 for pp in live_state[s.id] if pp.id not in fixed_ids))
            for sec in near:
                movable = [pp for pp in live_state[sec.id] if pp.id not in fixed_ids]
                if not movable:
                    continue
                done = False
                for mp in sorted(movable, key=lambda pp: pp.width):
                    for other in self.sections:
                        if other.id == sec.id:
                            continue
                        if section_fits_pallet(other, live_state[other.id], mp, strict_narrow):
                            live_state[sec.id].remove(mp)
                            live_state[other.id].append(mp)
                            assignment[mp.id] = other.id
                            if section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                                live_state[sec.id].append(p)
                                assignment[p.id] = sec.id
                                placed += 1
                                done = True
                                break
                            else:
                                live_state[other.id].remove(mp)
                                live_state[sec.id].append(mp)
                                assignment[mp.id] = sec.id
                    if done:
                        break
                if done:
                    break
        logger.info("NumPy virtual reslot: +%d", placed)

    # ==================================================================
    # Вспомогательные
    # ==================================================================

    def _section_by_id(self, sec_id: str) -> Optional[Section]:
        for sec in self.sections:
            if sec.id == sec_id:
                return sec
        return None
