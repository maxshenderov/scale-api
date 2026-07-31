"""LP Solver — simplex-метод (scipy.optimize.linprog) для типоразмерного распределения.

Наследует всю инфраструктуру NumpySolver (бакеты, типы, cost-матрица, дезагрегация),
заменяет только фазу allocation: LP relaxation + rounding + greedy fill.

Выбирается через solverType="lp" в настройках.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from solver.numpy_solver import NumpySolver

logger = logging.getLogger(__name__)


class LPSolver(NumpySolver):
    """Солвер на scipy linprog (simplex) + типоразмерная агрегация.

    Отличается от NumpySolver только фазой allocation:
    - LP relaxation (continuous) вместо 6-фазного greedy
    - Округление вниз + greedy fill остатка
    """

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
                "LP reslot: +%d movable existing паллет в пул размещения",
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
            "LP: типов=%d бакетов=%d паллет=%d (new=%d movable=%d)",
            n_types, n_buckets, len(pallets_to_place),
            len(self.new_pallets), len(self.movable_existing),
        )

        # 3. Cost-матрица и маски допустимых пар
        cost, feasible_strict, feasible_loose = self._build_cost_and_feasible(
            type_keys, type_data, bucket_keys, bucket_data, strict_narrow,
        )

        # 4. LP solve (scipy linprog)
        allocation = np.zeros((n_types, n_buckets), dtype=int)
        lp_ok = self._lp_solve_robust(
            allocation, type_keys, type_data, bucket_keys, bucket_data,
            cost, feasible_strict,
        )

        if not lp_ok:
            # Fallback на greedy при ошибке LP
            logger.warning("LP fallback to greedy")
            self._type_level_greedy(
                allocation, type_keys, type_data, bucket_keys, bucket_data,
                cost, feasible_strict, feasible_loose, strict_narrow,
            )

        initial_placed = int(allocation.sum())
        logger.info("LP: разместил %d/%d паллет", initial_placed, len(pallets_to_place))

        # 5. Дезагрегация
        self._disaggregate(
            allocation, type_keys, type_data, bucket_keys, bucket_data,
            assignment, strict_narrow,
        )

        # 6. Leftover resolution
        leftover = [p for p in pallets_to_place if assignment[p.id] is None]
        if leftover:
            live_state = self._build_live_state(assignment)
            before = len(leftover)
            self._resolve_leftovers(leftover, assignment, live_state, strict_narrow)
            after = len([p for p in leftover if assignment[p.id] is None])
            logger.info("LP leftover: +%d паллет", before - after)

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
            "LP: placed=%d/%d status=%s score=%.0f time=%.2fs (new=%d movable=%d)",
            final_placed, len(pallets_to_place), solver_status, score, self.solver_wall_time,
            len(self.new_pallets), len(self.movable_existing),
        )
        return assignment, solver_status, score

    # ==================================================================
    # LP Solver (robust — с правильными bounds)
    # ==================================================================

    def _lp_solve_robust(
        self,
        allocation: np.ndarray,
        type_keys: List[Tuple],
        type_data: List[dict],
        bucket_keys: List[Tuple],
        bucket_data: List[dict],
        cost: np.ndarray,
        feasible: np.ndarray,
    ) -> bool:
        """Solve type-level allocation with scipy LP relaxation.

        Returns True if LP succeeded, False if fallback needed.
        """
        try:
            from scipy.optimize import linprog
        except ImportError:
            logger.warning("scipy not available for LP")
            return False

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
            return True  # nothing to solve

        # --- Objective (negate for minimization) ---
        c = np.zeros(n_vars)
        for idx, (ti, bi) in enumerate(var_list):
            td = type_data[ti]
            obj = -100000.0  # maximize placed count
            # Narrow→narrow bonus
            if td["is_narrow"] and bucket_data[bi]["narrow_aisle"]:
                obj -= 10.0
            # Wide→narrow penalty
            if not td["is_narrow"] and bucket_data[bi]["narrow_aisle"]:
                obj += 5000.0
            c[idx] = obj

        # --- Bounds: (0, ub) tuples ---
        supply = np.array([td["count"] for td in type_data], dtype=np.float64)
        demand_count = np.array([bd["total_count"] for bd in bucket_data], dtype=np.float64)
        bounds = []
        for ti, bi in var_list:
            ub_val = min(float(supply[ti]), float(demand_count[bi]),
                         float(bucket_data[bi]["total_count"]))
            bounds.append((0.0, ub_val))

        # --- Constraints: A_ub @ x <= b_ub ---
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
            b_ub.append(float(supply[ti]))

        # 2. Bucket count
        for bi in range(n_buckets):
            row = np.zeros(n_vars)
            for ti in range(n_types):
                idx = var_idx.get((ti, bi))
                if idx is not None:
                    row[idx] = 1.0
            rows.append(row)
            b_ub.append(float(demand_count[bi]))

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
            b_ub.append(float(bd["total_width"] + bd["total_count"] * gap))

        # 4. Bucket weight (skip unconstrained)
        for bi in range(n_buckets):
            bd = bucket_data[bi]
            if math.isinf(bd["total_weight"]):
                continue
            row = np.zeros(n_vars)
            for ti in range(n_types):
                idx = var_idx.get((ti, bi))
                if idx is not None:
                    row[idx] = type_data[ti]["weight"]
            rows.append(row)
            b_ub.append(float(bd["total_weight"]))

        if not rows:
            return True

        A_ub = np.array(rows)
        b_ub = np.array(b_ub)

        logger.info("LP simplex: vars=%d constraints=%d", n_vars, len(b_ub))

        # --- Solve ---
        try:
            res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        except Exception as e:
            logger.warning("LP error: %s", e)
            return False

        if not res.success:
            logger.warning("LP failed: %s", res.message)
            return False

        # --- Round down ---
        for idx, (ti, bi) in enumerate(var_list):
            val = int(res.x[idx])
            if val > 0:
                allocation[ti, bi] = val

        lp_placed = int(allocation.sum())
        logger.info("LP simplex: objective=%.0f rounded=%d", -res.fun / 100000, lp_placed)

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

        # Greedy fill in priority order
        type_order = sorted(
            range(n_types),
            key=lambda ti: (
                not type_data[ti]["is_narrow"],
                -type_data[ti]["height"],
                -type_data[ti]["width"],
            ),
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

        return True
