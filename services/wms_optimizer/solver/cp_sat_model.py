"""CP-SAT модель Google OR-Tools для глобальной оптимизации (§9.1 ТЗ).

Решает задачу: Паллета → Секция.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from models.address import Address
from models.pallet import Pallet
from models.section import Section
from solver.config import num_search_workers
from solver.feasibility import compute_feasible_pairs, count_pairs

logger = logging.getLogger(__name__)


class CPSATSolver:
    """Обёртка над CP-SAT моделью для задачи размещения паллет."""

    # Масштабирующий коэффициент для вещественных весов в целочисленной модели
    SCALE = 1

    def __init__(
        self,
        sections: List[Section],
        new_pallets: List[Pallet],
        existing_pallets: List[Pallet],
        addresses: List[Address],
        settings,
        warm_start: Optional[Dict[str, str]] = None,
    ):
        self.sections = sections
        self.new_pallets = new_pallets
        self.existing_pallets = existing_pallets
        self.addresses = addresses
        self.settings = settings
        self.warm_start = warm_start or {}

        # Текущее расположение: pallet_id -> section_id
        self.pallet_current_section: Dict[str, str] = {}
        for addr in addresses:
            if addr.pallet_id is not None:
                self.pallet_current_section[addr.pallet_id] = addr.section_id

        # Индексы
        self.section_idx = {s.id: i for i, s in enumerate(sections)}

        # Текущий состав каждой секции (для расчёта потенциала)
        self.section_pallets: Dict[str, List[Pallet]] = {s.id: [] for s in sections}
        existing_map = {p.id: p for p in existing_pallets}
        for addr in addresses:
            if addr.pallet_id is not None and addr.pallet_id in existing_map:
                self.section_pallets[addr.section_id].append(existing_map[addr.pallet_id])

    def solve(self) -> Tuple[Dict[str, Optional[str]], str, float]:
        """Запускает решатель.

        Returns:
            assignment: {pallet_id: section_id | None}
            solver_status: "OPTIMAL" | "FEASIBLE" | "TIME_LIMIT" | "INFEASIBLE"
            score: значение целевой функции
        """
        # Порог переключения на агрегированную модель
        AGGREGATION_THRESHOLD = 100_000  # Было: 300_000. Понижено для более ранней агрегации

        blocked_pallets = [p for p in self.existing_pallets if not p.movable]
        movable_existing = [p for p in self.existing_pallets if p.movable]
        movable_pallets = self.new_pallets + movable_existing

        # Предфильтрация для оценки размера задачи
        feasible = compute_feasible_pairs(
            pallets=movable_pallets,
            sections=self.sections,
            strict_narrow=self.settings.strictNarrowAislePlacement,
            pallet_current_section=self.pallet_current_section,
            section_idx=self.section_idx,
        )
        total_pairs = count_pairs(feasible)

        # Выбор модели на основе размера задачи
        if total_pairs > AGGREGATION_THRESHOLD:
            logger.info(
                "Используем агрегированную модель (пар=%d > %d)",
                total_pairs, AGGREGATION_THRESHOLD
            )
            return self._solve_aggregated()
        else:
            logger.info(
                "Используем точную модель (пар=%d <= %d)",
                total_pairs, AGGREGATION_THRESHOLD
            )
            return self._solve_exact(movable_pallets, blocked_pallets, feasible)

    def _solve_exact(
        self,
        movable_pallets: List[Pallet],
        blocked_pallets: List[Pallet],
        feasible: Dict[str, List[int]],
    ) -> Tuple[Dict[str, Optional[str]], str, float]:
        """Точная CP-SAT модель (текущая реализация)."""
        model = cp_model.CpModel()
        sections = self.sections

        # Разделить движимые паллеты на new и existing
        movable_existing = [p for p in self.existing_pallets if p.movable]

        logger.info(
            "CP-SAT (точная модель): движимых паллет=%d заблокированных=%d секций=%d допустимых пар=%d",
            len(movable_pallets), len(blocked_pallets), len(sections), count_pairs(feasible),
        )

        # Константный вклад заблокированных паллет в каждую секцию — их место
        # уже занято и не подлежит пересмотру, поэтому вычитаем его из лимитов
        # секции один раз, вместо переменных с последующей фиксацией.
        blocked_count: Dict[str, int] = {}
        blocked_width: Dict[str, float] = {}
        blocked_weight: Dict[str, float] = {}
        for p in blocked_pallets:
            sec_id = self.pallet_current_section.get(p.id)
            if sec_id is None:
                continue
            blocked_count[sec_id] = blocked_count.get(sec_id, 0) + 1
            blocked_width[sec_id] = blocked_width.get(sec_id, 0.0) + p.width
            blocked_weight[sec_id] = blocked_weight.get(sec_id, 0.0) + p.weight

        # ---------------------------------------------------------------
        # Переменные X[p,s] = 1 если паллета p в секции s (только движимые)
        # ---------------------------------------------------------------
        X: Dict[Tuple[str, int], cp_model.IntVar] = {}
        for p in movable_pallets:
            for si in feasible[p.id]:
                X[(p.id, si)] = model.NewBoolVar(f"x_{p.id}_{si}")

        # R[p] = 1 если существующая (движимая) паллета физически перемещена
        R: Dict[str, cp_model.IntVar] = {}
        if self.settings.allowReslot:
            for p in movable_existing:
                R[p.id] = model.NewBoolVar(f"r_{p.id}")

        # ---------------------------------------------------------------
        # Ограничения
        # ---------------------------------------------------------------
        # Каждая паллета — максимум в одной секции
        for p in movable_pallets:
            vars_for_pallet = [X[(p.id, si)] for si in feasible[p.id]]
            if vars_for_pallet:
                model.Add(sum(vars_for_pallet) <= 1)

        # Вместимость секции (количество паллет <= max_pallets - заблокированные)
        for i, sec in enumerate(sections):
            vars_in_sec = [X[(p.id, i)] for p in movable_pallets if (p.id, i) in X]
            if vars_in_sec:
                model.Add(sum(vars_in_sec) <= sec.max_pallets - blocked_count.get(sec.id, 0))

        # Ширина §7.1 — бюджет секции уменьшен на ширину+гэп заблокированных
        for i, sec in enumerate(sections):
            vars_in_sec = [(p, X[(p.id, i)]) for p in movable_pallets if (p.id, i) in X]
            if vars_in_sec:
                # SUM(width*X) + (SUM(X)+1)*gap <= sec.width - заблокированные
                count_var = sum(xv for _, xv in vars_in_sec)
                width_sum = sum(int(p.width * self.SCALE) * xv for p, xv in vars_in_sec)
                gap = int(sec.gap_width * self.SCALE)
                fixed_count = blocked_count.get(sec.id, 0)
                fixed_width = int(blocked_width.get(sec.id, 0.0) * self.SCALE)
                width_limit = int(sec.width * self.SCALE) - fixed_width - fixed_count * gap
                model.Add(
                    width_sum + count_var * gap + gap <= width_limit
                )

        # Вес §7.2 (unlimited_weight секции пропускаем — предела нет)
        for i, sec in enumerate(sections):
            if math.isinf(sec.max_weight):
                continue
            vars_in_sec = [(p, X[(p.id, i)]) for p in movable_pallets if (p.id, i) in X]
            if vars_in_sec:
                weight_sum = sum(int(p.weight * self.SCALE) * xv for p, xv in vars_in_sec)
                fixed_weight = int(blocked_weight.get(sec.id, 0.0) * self.SCALE)
                model.Add(weight_sum <= int(sec.max_weight * self.SCALE) - fixed_weight)

        # Ограничения реслота для движимых существующих паллет
        if self.settings.allowReslot and R:
            # R[p] = 1 если NewSection(p) != OldSection(p)
            for p in movable_existing:
                old_sec = self.pallet_current_section.get(p.id)
                if old_sec is None:
                    continue
                old_sec_idx = self.section_idx.get(old_sec)
                for si in feasible[p.id]:
                    if si != old_sec_idx:
                        model.AddImplication(X[(p.id, si)], R[p.id])
                if old_sec_idx is not None and (p.id, old_sec_idx) in X:
                    model.AddImplication(X[(p.id, old_sec_idx)], R[p.id].Not())

            # SUM(R[p]) <= CurrentPalletCount * maxReslotPercent / 100
            reslot_vars = list(R.values())
            current_pallet_count = len(movable_existing)
            max_by_percent = math.floor(current_pallet_count * self.settings.maxReslotPercent / 100)
            model.Add(sum(reslot_vars) <= max_by_percent)
        else:
            # allowReslot=false: движимые существующие паллеты тоже не двигаем
            for p in movable_existing:
                old_sec = self.pallet_current_section.get(p.id)
                if old_sec is None:
                    continue
                old_sec_idx = self.section_idx.get(old_sec)
                if old_sec_idx is not None and (p.id, old_sec_idx) in X:
                    model.Add(X[(p.id, old_sec_idx)] == 1)
                for si in feasible[p.id]:
                    if si != old_sec_idx and (p.id, si) in X:
                        model.Add(X[(p.id, si)] == 0)

        # Лимит на количество операций плана: PUT + MOVE, без KEEP (§6 ТЗ).
        new_placed_sum = sum(
            X[(p.id, si)]
            for p in self.new_pallets
            for si in feasible[p.id]
            if (p.id, si) in X
        )
        move_sum = sum(R.values()) if R else 0
        model.Add(new_placed_sum + move_sum <= self.settings.maxOperations)

        # ---------------------------------------------------------------
        # Warm Start (§11 шаг 4)
        # ---------------------------------------------------------------
        for p in movable_pallets:
            hint_sec_id = self.warm_start.get(p.id)
            if hint_sec_id is not None:
                hint_si = self.section_idx.get(hint_sec_id)
                if hint_si is not None:
                    for si in feasible[p.id]:
                        if (p.id, si) in X:
                            model.AddHint(X[(p.id, si)], 1 if si == hint_si else 0)

        # ---------------------------------------------------------------
        # Целевая функция (§9.1) — максимизируем GlobalScore
        # ---------------------------------------------------------------
        placed_sum = sum(
            X[(p.id, si)]
            for p in movable_pallets
            for si in feasible[p.id]
            if (p.id, si) in X
        )

        section_move_sum = sum(R.values()) if R else 0

        # Приоритет узкопроходных секций для узкопроходных паллет (мягкий бонус,
        # действует и при strictNarrowAislePlacement=false — иначе CP-SAT равнодушен
        # к тому, какую из двух одинаково валидных секций выбрать).
        narrow_priority_sum = sum(
            X[(p.id, si)]
            for p in movable_pallets
            if p.is_narrow
            for si in feasible[p.id]
            if sections[si].narrow_aisle and (p.id, si) in X
        )

        gw_placed = 100000
        gw_section_move = 1000
        gw_narrow_priority = 10

        # Упрощённая целевая функция (потенциал в CP-SAT считается дорого — постобработка)
        objective = (
            gw_placed * placed_sum
            - gw_section_move * section_move_sum
            + gw_narrow_priority * narrow_priority_sum
        )
        model.Maximize(objective)

        # ---------------------------------------------------------------
        # Запуск решателя
        # ---------------------------------------------------------------
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(self.settings.timeLimitSeconds)
        solver.parameters.num_search_workers = num_search_workers()

        status = solver.Solve(model)

        self.solver_branches = solver.NumBranches()
        self.solver_conflicts = solver.NumConflicts()
        self.solver_wall_time = solver.WallTime()

        # ---------------------------------------------------------------
        # Разбор результата
        # ---------------------------------------------------------------
        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.UNKNOWN: "TIME_LIMIT",
            cp_model.MODEL_INVALID: "INFEASIBLE",
        }
        solver_status = status_map.get(status, "TIME_LIMIT")

        # Заблокированные паллеты никогда не были переменными — их место
        # известно заранее и не зависит от статуса решения.
        assignment: Dict[str, Optional[str]] = {
            p.id: self.pallet_current_section.get(p.id) for p in blocked_pallets
        }

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for p in movable_pallets:
                placed = False
                for si in feasible[p.id]:
                    if (p.id, si) in X and solver.Value(X[(p.id, si)]) == 1:
                        assignment[p.id] = sections[si].id
                        placed = True
                        break
                if not placed:
                    assignment[p.id] = None
        else:
            # INFEASIBLE или TIME_LIMIT без решения
            for p in movable_pallets:
                assignment[p.id] = None

        score = solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 0.0

        return assignment, solver_status, score

    def _solve_aggregated(self) -> Tuple[Dict[str, Optional[str]], str, float]:
        """Агрегированная CP-SAT модель — вызывает CPSATAggregatedSolver."""
        from solver.cp_sat_aggregated import CPSATAggregatedSolver

        aggregated_solver = CPSATAggregatedSolver(
            sections=self.sections,
            new_pallets=self.new_pallets,
            existing_pallets=self.existing_pallets,
            addresses=self.addresses,
            settings=self.settings,
            warm_start=self.warm_start,
        )

        assignment, solver_status, score = aggregated_solver.solve()

        # Копируем метрики решателя для логирования
        self.solver_branches = getattr(aggregated_solver, 'solver_branches', 0)
        self.solver_conflicts = getattr(aggregated_solver, 'solver_conflicts', 0)
        self.solver_wall_time = getattr(aggregated_solver, 'solver_wall_time', 0.0)

        return assignment, solver_status, score
