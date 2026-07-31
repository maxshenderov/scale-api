"""Агрегированная CP-SAT модель — Y[тип_паллеты, бакет_секции] (Фаза C).

Точная модель (cp_sat_model.py) создаёт булеву переменную на каждую
допустимую пару (паллета, секция) — на холодном складе с тысячами паллет и
секций это миллионы переменных (симметрия: одинаковые паллеты в одинаковые
секции неотличимы друг от друга, но CP-SAT всё равно перебирает их как
разные). Здесь вместо этого считаем СКОЛЬКО паллет каждого типоразмера идёт
в каждый "бакет" взаимозаменяемых секций — целочисленная переменная-счётчик,
а не булева матрица.

Область применения: только когда нет решения о реслоте (allowReslot=False
ИЛИ нет движимых существующих паллет) — см. global_optimizer.py. В этом
случае вся текущая занятость секций (включая движимые существующие паллеты)
неизменна и является константой, как заблокированные паллеты в Фазе B;
реслот здесь не поддерживается, потому что решение "эту существующую
паллету переместить или нет" — по природе относится к конкретному
экземпляру, а не к типу, и агрегация его не может корректно смоделировать.

Бакет секций — это группа секций с одинаковыми (height, depth,
max_lift_weight, eff_max_width, eff_max_depth, narrow_aisle, gap_width) И
одинаковым остатком вместимости (после вычета текущей занятости). Внутри
одного бакета все секции физически взаимозаменяемы для любой паллеты
данного типоразмера.

После решения — дезагрегация: раскладываем Y-счётчики по конкретным
паллетам и секциям, используя тот же проверенный предикат
`section_fits_pallet`, что и warm start и весь остальной код (аггрегатная
арифметика "сумма ширин уместится" не гарантирует физическую упаковку в
конкретные секции — классическая проблема бин-паковки, поэтому финальная
раскладка всегда сверяется с реальным remaining-space секции, а не считается
автоматически валидной только потому что сошлась по сумме).
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from models.address import Address
from models.pallet import Pallet
from models.section import Section
from optimizer.potential import section_fits_pallet
from solver.config import num_search_workers

logger = logging.getLogger(__name__)


def _dimension_fits(section: Section, pallet: Pallet, strict_narrow: bool) -> bool:
    """§7.3-7.5 без учёта текущей занятости секции — только габариты.

    Используется для поиска "почти влезло" секций под виртуальный реслот
    (_resolve_residual_with_reslot): section_fits_pallet учитывает текущий
    live_state и поэтому не годится для вопроса "подошла бы эта секция по
    габаритам, если бы в ней было свободнее".
    """
    if strict_narrow and pallet.is_narrow and not section.narrow_aisle:
        return False
    if pallet.height > section.height:
        return False
    if pallet.depth > section.depth:
        return False
    if pallet.weight > section.max_lift_weight:
        return False
    if pallet.width > section.eff_max_width:
        return False
    if pallet.depth > section.eff_max_depth:
        return False
    return True


TypeKey = Tuple[float, float, float, float]
BucketKey = Tuple[float, float, float, float, float, bool, float, int, float, float]

# Максимум физически идентичных секций в одном бакете. Сумма-ограничения по
# ширине/весу (см. докстринг модуля) — необходимое, но не достаточное условие
# физической упаковки; их зазор растёт с числом секций в бакете (классическая
# слабость LP-релаксации vector bin packing). На холодном складе секции с
# одинаковыми габаритами могут образовывать бакеты из десятков секций — режем
# такие группы на бакеты не крупнее этого числа, чтобы зазор не накапливался.
_BUCKET_CHUNK_SIZE = 1  # ОТКАТ: 5 вызвало регрессию (-125 паллет, +91 NARROW_AISLE_MISMATCH)

# Лимит времени точного остаточного дорешивания (см. _resolve_residual_exact) —
# независим от settings.timeLimitSeconds: та часть уже потрачена на основной
# агрегированный солвер, а остаточная задача (обычно пара сотен паллет) на
# порядки меньше исходной, поэтому ей достаточно небольшого фиксированного
# бюджета вместо повторного полного таймаута.
_RESIDUAL_TIME_LIMIT_SECONDS = 20.0

# Реслот-дорешивание — совместная модель над leftover + уже (виртуально)
# размещёнными паллетами в near-miss секциях. Задача комбинаторно тяжелее
# точного дорешивания той же размерности, поэтому у неё отдельный, чуть
# больший бюджет; движимый пул ограничен _RESLOT_MAX_CANDIDATE_SECTIONS,
# чтобы бюджет оставался реалистичным.
_RESLOT_TIME_LIMIT_SECONDS = 60.0
_RESLOT_MAX_CANDIDATE_SECTIONS = 150


class CPSATAggregatedSolver:
    """Агрегированная модель Y[тип_паллеты, бакет_секции] для холодного/no-reslot случая."""

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
        # Агрегированная модель не использует warm start точной модели —
        # эвристика уже встроена в порядок дезагрегации (широкие паллеты первыми).

        self.pallet_current_section: Dict[str, str] = {}
        for addr in addresses:
            if addr.pallet_id is not None:
                self.pallet_current_section[addr.pallet_id] = addr.section_id

        self.section_pallets: Dict[str, List[Pallet]] = {s.id: [] for s in sections}
        existing_map = {p.id: p for p in existing_pallets}
        for addr in addresses:
            if addr.pallet_id is not None and addr.pallet_id in existing_map:
                self.section_pallets[addr.section_id].append(existing_map[addr.pallet_id])

    # ------------------------------------------------------------------
    def _build_buckets(self) -> Tuple[List[BucketKey], List[dict]]:
        grouped: Dict[BucketKey, List[Section]] = {}
        for sec in self.sections:
            existing = self.section_pallets.get(sec.id, [])
            count = len(existing)
            width_sum = sum(p.width for p in existing)
            weight_sum = sum(p.weight for p in existing)

            remaining_count = sec.max_pallets - count
            if remaining_count <= 0:
                continue
            remaining_width = sec.width - width_sum - (count + 1) * sec.gap_width
            if remaining_width <= 0:
                continue
            remaining_weight = (
                math.inf if math.isinf(sec.max_weight) else sec.max_weight - weight_sum
            )
            if remaining_weight < 0:
                continue

            key: BucketKey = (
                sec.height, sec.depth, sec.max_lift_weight,
                sec.eff_max_width, sec.eff_max_depth, sec.narrow_aisle,
                round(sec.gap_width, 3),
                remaining_count, round(remaining_width, 3),
                math.inf if math.isinf(remaining_weight) else round(remaining_weight, 3),
            )
            grouped.setdefault(key, []).append(sec)

        # Дробим каждую группу физически идентичных секций на бакеты не крупнее
        # _BUCKET_CHUNK_SIZE (см. константу выше) — иначе один бакет холодного
        # склада объединяет десятки секций и зазор суммы-ограничения растёт с
        # его размером. bucket_keys и buckets — параллельные списки: один
        # физический key может встретиться несколько раз (по одному на чанк).
        bucket_keys: List[BucketKey] = []
        buckets: List[dict] = []
        for key, secs in grouped.items():
            remaining_count, remaining_width, remaining_weight = key[7], key[8], key[9]
            for start in range(0, len(secs), _BUCKET_CHUNK_SIZE):
                chunk = secs[start:start + _BUCKET_CHUNK_SIZE]
                bucket_keys.append(key)
                buckets.append({
                    "sections": chunk,
                    "total_count": remaining_count * len(chunk),
                    "total_width": remaining_width * len(chunk),
                    "total_weight": (
                        math.inf if math.isinf(remaining_weight) else remaining_weight * len(chunk)
                    ),
                    "narrow_aisle": key[5],
                })

        return bucket_keys, buckets

    @staticmethod
    def _type_key(p: Pallet) -> TypeKey:
        return (p.width, p.height, p.depth, p.weight)

    def solve(self) -> Tuple[Dict[str, Optional[str]], str, float]:
        settings = self.settings
        strict_narrow = settings.strictNarrowAislePlacement

        bucket_keys, buckets = self._build_buckets()

        type_groups: Dict[TypeKey, List[Pallet]] = {}
        for p in self.new_pallets:
            type_groups.setdefault(self._type_key(p), []).append(p)

        # Допустимые (тип, бакет) — то же правило §7.3-7.5 + узкий проход,
        # но проверяется один раз на представителя типа/бакета, а не на
        # каждую пару паллета×секция.
        feasible_types: Dict[TypeKey, List[int]] = {}
        for type_key, pallets_of_type in type_groups.items():
            w, h, d, wt = type_key
            is_narrow = w <= 1200 and d <= 1200
            feasible = []
            for bi, key in enumerate(bucket_keys):
                height, depth, max_lift_weight, eff_max_width, eff_max_depth, narrow_aisle = key[:6]
                if strict_narrow and is_narrow and not narrow_aisle:
                    continue
                if h <= height and d <= depth and wt <= max_lift_weight and w <= eff_max_width and d <= eff_max_depth:
                    feasible.append(bi)
            feasible_types[type_key] = feasible

        total_pairs = sum(len(v) for v in feasible_types.values())
        exact_equivalent = sum(len(v) for v in type_groups.values()) * len(self.sections)
        logger.info(
            "CP-SAT (агрегированная модель): типов паллет=%d бакетов секций=%d допустимых пар=%d "
            "(точная модель имела бы примерно %d пар)",
            len(type_groups), len(bucket_keys), total_pairs, exact_equivalent,
        )

        model = cp_model.CpModel()

        Y: Dict[Tuple[TypeKey, int], cp_model.IntVar] = {}
        for type_key, pallets_of_type in type_groups.items():
            n_type = len(pallets_of_type)
            for bi in feasible_types[type_key]:
                ub = min(n_type, buckets[bi]["total_count"])
                if ub <= 0:
                    continue
                var_name = f"y_{bucket_keys[bi][:6]}_{type_key}_{bi}"
                Y[(type_key, bi)] = model.NewIntVar(0, ub, var_name)

        # Не больше, чем есть паллет этого типа
        for type_key, pallets_of_type in type_groups.items():
            vars_for_type = [Y[(type_key, bi)] for bi in feasible_types[type_key] if (type_key, bi) in Y]
            if vars_for_type:
                model.Add(sum(vars_for_type) <= len(pallets_of_type))

        # Вместимость бакета: количество / ширина / вес
        for bi, key in enumerate(bucket_keys):
            bucket = buckets[bi]
            vars_in_bucket = [(type_key, Y[(type_key, bi)]) for type_key in type_groups if (type_key, bi) in Y]
            if not vars_in_bucket:
                continue

            count_sum = sum(yv for _, yv in vars_in_bucket)
            model.Add(count_sum <= bucket["total_count"])

            gap = int(round(key[6] * self.SCALE))
            width_sum = sum(int(round(type_key[0] * self.SCALE)) * yv for type_key, yv in vars_in_bucket)
            width_budget = int(round(bucket["total_width"] * self.SCALE))
            model.Add(width_sum + count_sum * gap <= width_budget)

            if not math.isinf(bucket["total_weight"]):
                weight_sum = sum(int(round(type_key[3] * self.SCALE)) * yv for type_key, yv in vars_in_bucket)
                model.Add(weight_sum <= int(round(bucket["total_weight"] * self.SCALE)))

        # Попытка штрафовать "открытые, но не дозаполненные" секции через
        # opened_b-индикатор на бакет испробована и откачена (Фаза D,
        # чанк=1): гистограмма показала, что разрыв с ручной раскладкой —
        # именно в форме (меньше секций "3 из 3"), но 1490 доп. bool-переменных
        # + reification резко замедлили solver.Solve() (branches=0, застрял на
        # presolve/первом решении) — итог 3191 вместо 3233 при том же 120с
        # лимите. Целевая функция остаётся только с narrow/wide-narrow
        # штрафами; консолидация переносится в постобработку (см.
        # _resolve_residual_exact — там же можно позволить двигать уже
        # размещённые паллеты, а не только искать место хвосту).


        # Лимит операций (§6) — в этой модели все Y это PUT, реслота нет.
        total_placed = sum(Y.values()) if Y else 0
        model.Add(total_placed <= settings.maxOperations)

        narrow_bonus_terms = [
            yv for (type_key, bi), yv in Y.items()
            if type_key[0] <= 1200 and type_key[2] <= 1200 and buckets[bi]["narrow_aisle"]
        ]
        narrow_bonus = sum(narrow_bonus_terms) if narrow_bonus_terms else 0

        # Широкопроходная паллета не подчиняется правилу узкого прохода
        # (см. PLACEMENT_RULES.md) — запрещать ей узкопроходные бакеты было
        # бы нарушением документированного поведения (испробовано и
        # откачено). Но узкопроходная вместимость — дефицитный ресурс,
        # нужный ИСКЛЮЧИТЕЛЬНО узкопроходным паллетам: "максимизировать
        # total_placed" безразлична к тому, кто из типов занял узкопроходный
        # бакет, солвер может отдать его широкой паллете (та же сумма
        # placed), оставив узкую без единственно доступного для неё места.
        # Мягкий штраф (не запрет — широким бакет остаётся доступен, если
        # альтернатив для них нет) отговаривает от этого, пока это не
        # снижает total_placed (gw_placed на порядок больше штрафа).
        wide_into_narrow_terms = [
            yv for (type_key, bi), yv in Y.items()
            if not (type_key[0] <= 1200 and type_key[2] <= 1200) and buckets[bi]["narrow_aisle"]
        ]
        wide_into_narrow = sum(wide_into_narrow_terms) if wide_into_narrow_terms else 0

        gw_placed = 100000
        gw_narrow_priority = 10
        gw_wide_narrow_penalty = 5000
        model.Maximize(
            gw_placed * total_placed
            + gw_narrow_priority * narrow_bonus
            - gw_wide_narrow_penalty * wide_into_narrow
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(settings.timeLimitSeconds)
        solver.parameters.num_search_workers = num_search_workers()
        status = solver.Solve(model)

        self.solver_branches = solver.NumBranches()
        self.solver_conflicts = solver.NumConflicts()
        self.solver_wall_time = solver.WallTime()

        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.UNKNOWN: "TIME_LIMIT",
            cp_model.MODEL_INVALID: "INFEASIBLE",
        }
        solver_status = status_map.get(status, "TIME_LIMIT")

        # Существующие паллеты в этой модели всегда константа — реслота нет
        # (см. docstring: агрегированная модель применяется только когда нет
        # решений о реслоте).
        assignment: Dict[str, Optional[str]] = {
            p.id: self.pallet_current_section.get(p.id) for p in self.existing_pallets
        }
        for p in self.new_pallets:
            assignment[p.id] = None

        score = 0.0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            counts: Dict[Tuple[TypeKey, int], int] = {}
            for key, yv in Y.items():
                val = solver.Value(yv)
                if val > 0:
                    counts[key] = val
            logger.info(
                "CP-SAT (агрегированная модель): солвер решил разместить %d паллет (из %d новых)",
                sum(counts.values()), len(self.new_pallets),
            )
            self._disaggregate(counts, type_groups, buckets, bucket_keys, assignment, strict_narrow)
            score = solver.ObjectiveValue()

        return assignment, solver_status, score

    # ------------------------------------------------------------------
    def _disaggregate(
        self,
        counts: Dict[Tuple[TypeKey, int], int],
        type_groups: Dict[TypeKey, List[Pallet]],
        buckets: List[dict],
        bucket_keys: List[BucketKey],
        assignment: Dict[str, Optional[str]],
        strict_narrow: bool,
    ) -> None:
        """Раскладывает Y-счётчики по конкретным секциям и паллетам.

        Y[тип, бакет]=n — солвер решил "n паллет этого типа идут в этот
        бакет", но бакет — это АГРЕГАТ из нескольких секций с одинаковым
        остатком вместимости по сумме; совпадение по сумме не гарантирует,
        что n конкретных паллет физически влезут именно в секции этого
        бакета (see: 2 бака по 10, паллеты 7+7+6=20 — сумма совпадает,
        упаковка невозможна). Поэтому: пробуем разместить внутри "родного"
        бакета через реальную fit-проверку, а то, что не влезло — не
        считаем сразу непомещённым, а пробуем в остальных секциях склада.
        Это не даёт разместить больше, чем решил солвер (maxOperations не
        нарушается) — просто перераспределяет уже учтённые n штук туда,
        где они физически встали.
        """
        remaining_by_type: Dict[TypeKey, List[Pallet]] = {k: list(v) for k, v in type_groups.items()}
        live_state: Dict[str, List[Pallet]] = {
            sec.id: list(self.section_pallets.get(sec.id, [])) for sec in self.sections
        }

        mismatch_leftover: List[Pallet] = []

        # Собираем по бакету ВСЕ типы, которые солвер туда назначил, и
        # упаковываем их совместно — если раскладывать по одному типу за
        # раз (как раньше), первый обработанный тип может съесть ширину,
        # которая нужна была для точного набора вместе со вторым (например
        # секция шириной под 800+800+900, а мы сначала займём её тремя
        # 900 и для 800 просто не останется места, хотя по сумме бакета
        # всё сходится) — та же проблема суммы, что в докстринге модуля.
        # First-Fit-Decreasing (самые широкие первыми, первая секция с
        # местом) — стандартная эвристика бин-паковки для таких наборов.
        by_bucket: Dict[int, List[Pallet]] = {}
        for (type_key, bi), n in counts.items():
            pool = remaining_by_type[type_key]
            to_place = [pool.pop() for _ in range(min(n, len(pool)))]
            by_bucket.setdefault(bi, []).extend(to_place)

        for bi, pallets_for_bucket in by_bucket.items():
            bucket_sections = buckets[bi]["sections"]
            leftover_this_bucket: List[Pallet] = []
            for candidate in sorted(pallets_for_bucket, key=lambda p: -p.width):
                # Best-fit (не first-fit): предпочитаем секцию, где уже больше
                # паллет — это "дозаполняет" секции до max_pallets вместо того,
                # чтобы тонко размазывать по многим секциям. Ручная раскладка
                # человека даёт заметно больше секций "3 из 3" и меньше "1 из 3"
                # при том же общем количестве размещённых — эта эвристика
                # воспроизводит именно такое поведение.
                best_sec = None
                best_occ = -1
                for sec in bucket_sections:
                    if section_fits_pallet(sec, live_state[sec.id], candidate, strict_narrow):
                        occ = len(live_state[sec.id])
                        if occ > best_occ:
                            best_occ = occ
                            best_sec = sec
                if best_sec is not None:
                    live_state[best_sec.id].append(candidate)
                    assignment[candidate.id] = best_sec.id
                else:
                    leftover_this_bucket.append(candidate)
            mismatch_leftover.extend(leftover_this_bucket)

        # Паллеты, которым не хватило места в "родном" бакете (редкий край
        # случай неидеальной суб-упаковки) — пробуют оставшиеся секции всего
        # склада. Узкопроходные паллеты сначала (их выбор секций уже ограничен
        # правилом узкого проходa — если сначала пристроить широкие паллеты в
        # узкопроходные секции, узким может не хватить места); внутри своего
        # класса — самые широкие сначала (та же логика, что в FFD warm start).
        # Широким паллетам узкопроходные секции НЕ запрещены (см. PLACEMENT_RULES.md
        # "Широкопроходная паллета... не подчиняется этому правилу") — здесь
        # только порядок обработки, не резервирование.
        if mismatch_leftover:
            sections_sorted = sorted(self.sections, key=lambda s: (not s.narrow_aisle, -s.width))
            fallback_placed = 0
            for pallet in sorted(mismatch_leftover, key=lambda p: (not p.is_narrow, -p.width)):
                # Тот же best-fit, что и в native-бакет проходе выше — иначе
                # fallback-паллеты открывают новые полу-пустые секции вместо
                # дозаполнения уже начатых.
                best_sec = None
                best_occ = -1
                for sec in sections_sorted:
                    if section_fits_pallet(sec, live_state[sec.id], pallet, strict_narrow):
                        occ = len(live_state[sec.id])
                        if occ > best_occ:
                            best_occ = occ
                            best_sec = sec
                if best_sec is not None:
                    live_state[best_sec.id].append(pallet)
                    assignment[pallet.id] = best_sec.id
                    fallback_placed += 1
            logger.info(
                "CP-SAT (агрегированная модель) дезагрегация: не влезло в родной бакет=%d, "
                "из них разместил fallback по складу=%d",
                len(mismatch_leftover), fallback_placed,
            )

        # Паллеты, чей тип солвер вообще не выбрал ни в один бакет (сумма Y по
        # типу < числа паллет этого типа) — раньше терялись безвозвратно: они
        # никогда не попадали ни в by_bucket, ни в mismatch_leftover, поэтому
        # и дорешивание их не видело. При _BUCKET_CHUNK_SIZE=1 сумма-ограничение
        # по бакету точна (один бакет = одна секция), так что "не влезло в
        # родной бакет" почти никогда не срабатывает — весь разрыв с ручной
        # раскладкой на практике здесь: солвер в лимит времени не насытил Y
        # для части типов, хотя физическая вместимость есть.
        never_selected = [p for pool in remaining_by_type.values() for p in pool]

        leftover_final = [p for p in mismatch_leftover if assignment[p.id] is None] + never_selected
        if leftover_final:
            self._resolve_residual_exact(leftover_final, live_state, assignment, strict_narrow)
            still_unplaced = [p for p in leftover_final if assignment[p.id] is None]
            if still_unplaced:
                self._resolve_residual_with_consolidation(still_unplaced, live_state, assignment, strict_narrow)
                still_unplaced = [p for p in still_unplaced if assignment[p.id] is None]
            if still_unplaced:
                self._resolve_residual_with_reslot(still_unplaced, live_state, assignment, strict_narrow)

    # ------------------------------------------------------------------
    def _resolve_residual_exact(
        self,
        leftover: List[Pallet],
        live_state: Dict[str, List[Pallet]],
        assignment: Dict[str, Optional[str]],
        strict_narrow: bool,
    ) -> None:
        """Точная (неагрегированная) CP-SAT модель — последний хвост после дезагрегации.

        Y[тип, бакет] решателя — сумма-ограничение (см. докстринг модуля),
        необходимое, но не достаточное условие физической упаковки: greedy
        дезагрегация (родной бакет + fallback по складу) регулярно не
        реализует часть "обещанных" солвером мест. leftover здесь — обычно
        лишь пара сотен паллет, поэтому точная BoolVar-на-пару модель
        (как cp_sat_model.py, но без реслота и без заблокированных/движимых
        существующих — вся текущая занятость live_state уже константа) не
        воспроизводит исходный холостой перебор миллионов пар: допустимых
        пар здесь на порядки меньше FEASIBLE_PAIRS_THRESHOLD.
        """
        model = cp_model.CpModel()

        feasible: Dict[str, List[int]] = {}
        X: Dict[Tuple[str, int], cp_model.IntVar] = {}
        for p in leftover:
            feas = [
                si for si, sec in enumerate(self.sections)
                if section_fits_pallet(sec, live_state[sec.id], p, strict_narrow)
            ]
            feasible[p.id] = feas
            for si in feas:
                X[(p.id, si)] = model.NewBoolVar(f"rx_{p.id}_{si}")

        if not X:
            logger.info(
                "CP-SAT (агрегированная модель) точное дорешивание: паллет=%d допустимых пар=0 "
                "(остаточной вместимости не нашлось), окончательно непомещённых=%d",
                len(leftover), len(leftover),
            )
            return

        for p in leftover:
            vars_for_p = [X[(p.id, si)] for si in feasible[p.id]]
            if vars_for_p:
                model.Add(sum(vars_for_p) <= 1)

        for si, sec in enumerate(self.sections):
            vars_in_sec = [(p, X[(p.id, si)]) for p in leftover if (p.id, si) in X]
            if not vars_in_sec:
                continue
            existing = live_state[sec.id]
            n0 = len(existing)
            width_sum_existing = sum(pp.width for pp in existing)
            weight_sum_existing = sum(pp.weight for pp in existing)

            count_var = sum(xv for _, xv in vars_in_sec)
            model.Add(count_var <= sec.max_pallets - n0)

            gap = int(round(sec.gap_width * self.SCALE))
            width_var = sum(int(round(p.width * self.SCALE)) * xv for p, xv in vars_in_sec)
            remaining_width = sec.width - width_sum_existing - (n0 + 1) * sec.gap_width
            model.Add(width_var + count_var * gap <= int(round(remaining_width * self.SCALE)))

            if not math.isinf(sec.max_weight):
                weight_var = sum(int(round(p.weight * self.SCALE)) * xv for p, xv in vars_in_sec)
                remaining_weight = sec.max_weight - weight_sum_existing
                model.Add(weight_var <= int(round(remaining_weight * self.SCALE)))

        pallet_by_id = {p.id: p for p in leftover}
        narrow_bonus_terms = [
            xv for (pid, si), xv in X.items()
            if pallet_by_id[pid].is_narrow and self.sections[si].narrow_aisle
        ]
        placed_sum = sum(X.values())
        narrow_bonus = sum(narrow_bonus_terms) if narrow_bonus_terms else 0

        gw_placed = 100000
        gw_narrow_priority = 10
        model.Maximize(gw_placed * placed_sum + gw_narrow_priority * narrow_bonus)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = _RESIDUAL_TIME_LIMIT_SECONDS
        solver.parameters.num_search_workers = num_search_workers()
        status = solver.Solve(model)

        placed = 0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for p in leftover:
                for si in feasible[p.id]:
                    if solver.Value(X[(p.id, si)]) == 1:
                        sec = self.sections[si]
                        live_state[sec.id].append(p)
                        assignment[p.id] = sec.id
                        placed += 1
                        break

        logger.info(
            "CP-SAT (агрегированная модель) точное дорешивание: паллет=%d допустимых пар=%d "
            "статус=%s разместил=%d, окончательно непомещённых=%d",
            len(leftover), len(X), solver.StatusName(status), placed, len(leftover) - placed,
        )

    # ------------------------------------------------------------------
    def _resolve_residual_with_consolidation(
        self,
        leftover: List[Pallet],
        live_state: Dict[str, List[Pallet]],
        assignment: Dict[str, Optional[str]],
        strict_narrow: bool,
    ) -> None:
        """Консолидация недозаполненных секций — освобождение целых секций
        путём перемещения их редких жильцов в другие уже начатые секции.

        Идея: секция с остатком свободной ширины ≥1/3 её полной ширины (без
        учёта зазоров) — кандидат на консолидацию. Если там стоит одна паллета
        (или две шириной ~900 каждая), попробуем найти им место в других
        секциях того же типоразмера, освободив целую секцию для leftover.

        Работает только на виртуально размещённых new_pallets (те, что сверх
        self.section_pallets) — реально существующие паллеты не трогаем.
        """
        fixed_ids = {p.id for pallets in self.section_pallets.values() for p in pallets}

        # Найти недозаполненные секции: виртуальные жильцы есть, но осталось ≥1/3.
        candidate_sections = []
        for sec in self.sections:
            occupants = [p for p in live_state[sec.id] if p.id not in fixed_ids]
            if not occupants:
                continue
            fixed = self.section_pallets.get(sec.id, [])
            used_width = sum(p.width for p in fixed) + sum(p.width for p in occupants)
            free_width_no_gap = sec.width - used_width
            if free_width_no_gap >= sec.width / 3.0:
                candidate_sections.append((sec, occupants))

        if not candidate_sections:
            logger.info(
                "CP-SAT (агрегированная модель) консолидация: недозаполненных секций не найдено, "
                "окончательно непомещённых=%d",
                len(leftover),
            )
            return

        # Попробуем переместить жильцов кандидатов в другие секции того же
        # типоразмера (чтобы освободить целую секцию). Сортируем секции по
        # числу жильцов: меньше жильцов → проще освободить.
        candidate_sections.sort(key=lambda x: len(x[1]))

        freed_sections = []
        for sec, occupants in candidate_sections:
            # Попробуем найти каждому occupant новое место среди УЖЕ начатых
            # секций (не открываем новые — иначе это не консолидация, а просто
            # перекладывание в пустое).
            can_relocate_all = True
            tentative_moves = []
            for p in occupants:
                found = False
                for other_sec in self.sections:
                    if other_sec.id == sec.id:
                        continue
                    # Проверяем что other_sec уже начата (есть хотя бы один жилец).
                    if not live_state[other_sec.id]:
                        continue
                    if section_fits_pallet(other_sec, live_state[other_sec.id], p, strict_narrow):
                        tentative_moves.append((p, other_sec))
                        found = True
                        break
                if not found:
                    can_relocate_all = False
                    break

            if can_relocate_all and len(tentative_moves) == len(occupants):
                # Применяем перемещения.
                for p, target in tentative_moves:
                    live_state[sec.id].remove(p)
                    live_state[target.id].append(p)
                    assignment[p.id] = target.id
                freed_sections.append(sec.id)

        if not freed_sections:
            logger.info(
                "CP-SAT (агрегированная модель) консолидация: кандидатов=%d, ни одной не удалось освободить, "
                "окончательно непомещённых=%d",
                len(candidate_sections), len(leftover),
            )
            return

        # Теперь freed_sections пусты — попробуем разместить leftover туда.
        placed = 0
        for p in leftover:
            if assignment[p.id] is not None:
                continue
            for sec in self.sections:
                if sec.id not in freed_sections:
                    continue
                if section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                    live_state[sec.id].append(p)
                    assignment[p.id] = sec.id
                    placed += 1
                    break

        logger.info(
            "CP-SAT (агрегированная модель) консолидация: кандидатов=%d освобождено=%d разместил=%d, "
            "окончательно непомещённых=%d",
            len(candidate_sections), len(freed_sections), placed, len(leftover) - placed,
        )

    # ------------------------------------------------------------------
    def _resolve_residual_with_reslot(
        self,
        leftover: List[Pallet],
        live_state: Dict[str, List[Pallet]],
        assignment: Dict[str, Optional[str]],
        strict_narrow: bool,
    ) -> None:
        """Последний хвост: виртуальный реслот уже (пока не физически)
        размещённых new_pallets, чтобы освободить место под leftover.

        Холодный старт: всё, что лежит в live_state сверх self.section_pallets
        (реальной, физической занятости на момент запроса) — это решения,
        принятые ЭТИМ прогоном (bucket-пасс/fallback/_resolve_residual_exact).
        Ничего из этого ещё не закоммичено, поэтому пересмотреть их совместно
        с leftover ничего не стоит: в итоговом плане это всё равно обычные
        PUT-операции на финальный адрес, а не MOVE (реальной перестановки не
        произойдёт — см. обсуждение с пользователем).

        Область действия ограничена секциями, которые по чистым габаритам
        (_dimension_fits, без учёта занятости) подошли бы хотя бы одной
        leftover-паллете, но фактически (section_fits_pallet, с учётом
        занятости) — нет. Это именно секции "почти влезло, не хватило места
        из-за уже расставленных", а не весь склад — так движимый пул остаётся
        маленьким (десятки-сотни, а не тысячи) и модель остаётся дешёвой.

        Безопасность от регрессии: у каждой движимой (уже размещённой)
        паллеты её текущая секция всегда входит в допустимые — "все остаются
        на месте" всегда физически совместимо с ограничениями модели (это
        было верно и до пересмотра), поэтому joint-модель не может стать
        INFEASIBLE и никогда не отнимает уже занятое место без замены.
        """
        # Дедупликация по типу паллеты — секции-кандидаты одинаковы для всех
        # экземпляров одного типа (section_fits_pallet зависит только от
        # занятости секции, не от конкретного id паллеты), поэтому сканируем
        # склад один раз на тип, а не один раз на каждую из leftover-паллет.
        leftover_by_type: Dict[TypeKey, Pallet] = {}
        for p in leftover:
            leftover_by_type.setdefault(self._type_key(p), p)

        near_miss_idx: set = set()
        for p in leftover_by_type.values():
            for si, sec in enumerate(self.sections):
                if not _dimension_fits(sec, p, strict_narrow):
                    continue
                if not section_fits_pallet(sec, live_state[sec.id], p, strict_narrow):
                    near_miss_idx.add(si)

        if not near_miss_idx:
            logger.info(
                "CP-SAT (агрегированная модель) реслот-дорешивание: паллет=%d секций-кандидатов=0, "
                "окончательно непомещённых=%d",
                len(leftover), len(leftover),
            )
            return

        # Модель растёт с движимым пулом, а не с числом секций-кандидатов —
        # ограничиваем пул, отдавая приоритет секциям с МЕНЬШИМ числом уже
        # (виртуально) занятых паллет: их дешевле пересобрать, и они первыми
        # освобождают слот. Без этой границы near-miss по распространённому
        # типоразмеру секции затягивает в модель весь склад (наблюдалось:
        # 759 секций / 1507 движимых паллет на 176 leftover → солвер не
        # успевал найти решение за отведённое время).
        def _movable_count(si: int) -> int:
            sec = self.sections[si]
            fixed_ids = {p.id for p in self.section_pallets.get(sec.id, [])}
            return sum(1 for p in live_state[sec.id] if p.id not in fixed_ids)

        if len(near_miss_idx) > _RESLOT_MAX_CANDIDATE_SECTIONS:
            ranked = sorted(near_miss_idx, key=_movable_count)
            near_miss_idx = set(ranked[:_RESLOT_MAX_CANDIDATE_SECTIONS])

        movable_pool: List[Pallet] = []
        for si in near_miss_idx:
            sec = self.sections[si]
            fixed_ids = {p.id for p in self.section_pallets.get(sec.id, [])}
            for p in live_state[sec.id]:
                if p.id not in fixed_ids:
                    movable_pool.append(p)

        if not movable_pool:
            logger.info(
                "CP-SAT (агрегированная модель) реслот-дорешивание: паллет=%d секций-кандидатов=%d "
                "движимого пула нет (занято реальной занятостью), окончательно непомещённых=%d",
                len(leftover), len(near_miss_idx), len(leftover),
            )
            return

        sec_id_to_si = {sec.id: si for si, sec in enumerate(self.sections)}

        model = cp_model.CpModel()
        X: Dict[Tuple[str, int], cp_model.IntVar] = {}
        feasible: Dict[str, List[int]] = {}

        for p in leftover:
            feas = [si for si in near_miss_idx if _dimension_fits(self.sections[si], p, strict_narrow)]
            feasible[p.id] = feas
            for si in feas:
                X[(p.id, si)] = model.NewBoolVar(f"rr_{p.id}_{si}")

        for p in movable_pool:
            cur_si = sec_id_to_si[assignment[p.id]]
            feas_set = {si for si in near_miss_idx if _dimension_fits(self.sections[si], p, strict_narrow)}
            feas_set.add(cur_si)
            feas = sorted(feas_set)
            feasible[p.id] = feas
            for si in feas:
                X[(p.id, si)] = model.NewBoolVar(f"rr_{p.id}_{si}")

        for p in leftover:
            vars_for_p = [X[(p.id, si)] for si in feasible[p.id]]
            if vars_for_p:
                model.Add(sum(vars_for_p) <= 1)

        # Движимая паллета обязана остаться размещённой — РОВНО одно место
        # (её текущая секция всегда в feasible, поэтому это ограничение не
        # может сделать модель невыполнимой).
        for p in movable_pool:
            vars_for_p = [X[(p.id, si)] for si in feasible[p.id]]
            model.Add(sum(vars_for_p) == 1)

        all_pallets = leftover + movable_pool
        for si in near_miss_idx:
            sec = self.sections[si]
            vars_in_sec = [(p, X[(p.id, si)]) for p in all_pallets if (p.id, si) in X]
            if not vars_in_sec:
                continue
            fixed = self.section_pallets.get(sec.id, [])
            n0 = len(fixed)
            width0 = sum(pp.width for pp in fixed)
            weight0 = sum(pp.weight for pp in fixed)

            count_var = sum(xv for _, xv in vars_in_sec)
            model.Add(count_var <= sec.max_pallets - n0)

            gap = int(round(sec.gap_width * self.SCALE))
            width_var = sum(int(round(p.width * self.SCALE)) * xv for p, xv in vars_in_sec)
            remaining_width = sec.width - width0 - (n0 + 1) * sec.gap_width
            model.Add(width_var + count_var * gap <= int(round(remaining_width * self.SCALE)))

            if not math.isinf(sec.max_weight):
                weight_var = sum(int(round(p.weight * self.SCALE)) * xv for p, xv in vars_in_sec)
                remaining_weight = sec.max_weight - weight0
                model.Add(weight_var <= int(round(remaining_weight * self.SCALE)))

        leftover_ids = {p.id for p in leftover}
        pallet_by_id = {p.id: p for p in all_pallets}
        narrow_bonus_terms = [
            xv for (pid, si), xv in X.items()
            if pid in leftover_ids and pallet_by_id[pid].is_narrow and self.sections[si].narrow_aisle
        ]
        placed_sum = sum(X[(p.id, si)] for p in leftover for si in feasible[p.id])
        narrow_bonus = sum(narrow_bonus_terms) if narrow_bonus_terms else 0

        gw_placed = 100000
        gw_narrow_priority = 10
        model.Maximize(gw_placed * placed_sum + gw_narrow_priority * narrow_bonus)

        # Warm-start: "все остаются на месте, leftover не размещён" — это
        # решение мы уже знаем как гарантированно допустимое (см. докстринг
        # метода), но CP-SAT об этом не знает и на большом движимом пуле может
        # потратить весь бюджет времени на поиск ХОТЯ БЫ какого-то допустимого
        # решения (статус UNKNOWN без единого incumbent). Явная подсказка даёт
        # солверу стартовое решение сразу, и весь бюджет уходит на его
        # улучшение, а не на первичный поиск допустимости.
        for p in movable_pool:
            cur_si = sec_id_to_si[assignment[p.id]]
            for si in feasible[p.id]:
                model.AddHint(X[(p.id, si)], 1 if si == cur_si else 0)
        for p in leftover:
            for si in feasible[p.id]:
                model.AddHint(X[(p.id, si)], 0)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = _RESLOT_TIME_LIMIT_SECONDS
        solver.parameters.num_search_workers = num_search_workers()
        status = solver.Solve(model)

        placed = 0
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            new_positions: Dict[str, int] = {}
            for p in all_pallets:
                for si in feasible[p.id]:
                    if solver.Value(X[(p.id, si)]) == 1:
                        new_positions[p.id] = si
                        break

            # live_state задействованных секций перестраиваем целиком из
            # решения солвера (не инкрементально) — иначе "снятые" со старого
            # места движимые паллеты продолжат считаться занимающими его.
            for si in near_miss_idx:
                sec = self.sections[si]
                live_state[sec.id] = list(self.section_pallets.get(sec.id, []))

            for p in movable_pool:
                si = new_positions[p.id]
                sec = self.sections[si]
                live_state[sec.id].append(p)
                assignment[p.id] = sec.id

            for p in leftover:
                si = new_positions.get(p.id)
                if si is None:
                    continue
                sec = self.sections[si]
                live_state[sec.id].append(p)
                assignment[p.id] = sec.id
                placed += 1

        logger.info(
            "CP-SAT (агрегированная модель) реслот-дорешивание: паллет=%d движимый_пул=%d "
            "секций-кандидатов=%d статус=%s разместил=%d objective=%s bound=%s branches=%d "
            "wallTime=%.1fs, окончательно непомещённых=%d",
            len(leftover), len(movable_pool), len(near_miss_idx),
            solver.StatusName(status), placed,
            solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else "n/a",
            solver.BestObjectiveBound(), solver.NumBranches(), solver.WallTime(),
            len(leftover) - placed,
        )
