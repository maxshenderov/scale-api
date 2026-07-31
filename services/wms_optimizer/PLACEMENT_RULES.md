# WMS Optimizer — правила размещения (по факту кода)

> Каждый пункт ниже привязан к конкретному файлу и строке в `services/wms_optimizer/`.
> Ничего не выведено логически из названий полей — только то, что реально проверяется в коде.
> Версия 2.0 — переписана после ревью, предыдущая версия 1.0 содержала придуманные правила
> (AccessLevel-совместимость, scoring адреса по Floor/AccessLevel/NarrowAisle) — они удалены.

---

## Оглавление

1. [Что реально используется в логике, а что просто хранится](#что-используется)
2. [Узкопроходные стеллажи (narrowAisle)](#узкопроходные-стеллажи)
3. [Отбор секций, где паллета физически может стоять](#отбор-секций)
4. [Ограничения секции (CP-SAT)](#ограничения-cp-sat)
5. [Заблокированные и движимые паллеты](#заблокированные-паллеты)
6. [Реслот (allowReslot / maxReslotPercent)](#реслот)
7. [Лимит операций (maxOperations)](#лимит-операций)
8. [Warm Start (First Fit Decreasing)](#warm-start)
9. [Выбор адреса внутри секции — правило 1С](#выбор-адреса)
10. [Причины отказа в размещении (notPlaced.reason)](#причины-отказа)
11. [Целевая функция (score)](#целевая-функция)
12. [Валидация запроса](#валидация-запроса)

---

## Что используется

Поля `accessLevel`, `accessTime`, `floor` **хранятся** в моделях
`Section`/`Pallet` (`models/section.py`, `models/pallet.py`), приходят из occupancy
(`api/schemas.py`), но **не участвуют ни в одной проверке размещения, ни в scoring**.

Проверено `grep` по всему `optimizer/`, `solver/`, `validation/`: эти три поля там
**не встречаются**. Комментарий в коде подтверждает это прямо:

`models/pallet.py:25-27`:
```python
# Для новых паллет — приоритет доступа (резерв на будущее: сортировка секций
# по accessLevel/accessTime в зависимости от значения 1 или 2).
access_level: int = 1
```

Это зарезервированные поля на будущее. Сейчас они не влияют на то, куда попадёт паллета.

`restricted=True` — единственное исключение: такие секции **полностью выбрасываются**
ещё на этапе построения модели и в оптимизации не участвуют вообще
(`models/occupancy_builder.py:33-35`):
```python
for row in occupancy:
    if row.restricted:
        continue
```

---

## Узкопроходные стеллажи

**Узкопроходная паллета** — паллета, у которой `width ≤ 1200` И `depth ≤ 1200` (мм).
Вычисляется на сервере (`models/pallet.py`, свойство `is_narrow`), без отдельного входного
поля — только по ширине/глубине паллеты, переданным в `newPallets`/occupancy.

**Узкопроходная секция** — секция с `narrowAisle=true` в occupancy (`models/section.py`,
поле `narrow_aisle`).

### Параметр `settings.strictNarrowAislePlacement` (bool, default `true`)

| Значение | Поведение |
|---|---|
| `true` (default) | Узкопроходная паллета размещается **ТОЛЬКО** в секции с `narrowAisle=true`. Если все узкопроходные секции заняты → паллета попадает в `notPlaced` с причиной `NARROW_AISLE_MISMATCH`, даже если есть свободные широкопроходные секции. |
| `false` | Узкопроходные секции проверяются первыми (приоритет), но если все заняты — паллета размещается в широкопроходную секцию как запасной вариант. |

Правило проверяется в трёх местах пайплайна:

- `optimizer/potential.py:section_fits_pallet` (параметр `strict_narrow`) — используется FFD
  warm start и постпроцессингом причин отказа.
- `solver/cp_sat_model.py` (предфильтрация feasible-пар) — при `strictNarrowAislePlacement=true`
  текущая секция уже стоящей паллеты **всегда** остаётся допустимой для неё независимо от
  правила, иначе она выпадает из модели целиком и её вес/ширина не учитываются в ограничениях
  секции (см. [Ограничения CP-SAT](#ограничения-cp-sat)).
- `optimizer/global_optimizer.py:_determine_not_placed_reason` — формирует причину
  `NARROW_AISLE_MISMATCH`, приоритетную над остальными причинами отказа.

### Приоритет узкопроходных секций

Независимо от `strictNarrowAislePlacement`, узкопроходные секции имеют более высокий приоритет
при выборе, куда поставить узкопроходную паллету:

- **FFD warm start** (`solver/warm_start.py`): секции сортируются
  `sorted(sections, key=lambda s: (not s.narrow_aisle, -s.width))` — сначала все узкопроходные,
  потом остальные по убыванию ширины.
- **CP-SAT целевая функция** (`solver/cp_sat_model.py`): добавлен бонус
  `+10 * narrow_priority_sum` за размещение узкопроходной паллеты в узкопроходную секцию —
  без этого бонуса решателю всё равно, какую из двух одинаково валидных секций выбрать
  (см. [Целевая функция](#целевая-функция)).

Широкопроходная паллета (`width > 1200` ИЛИ `depth > 1200`) не подчиняется этому правилу —
может размещаться в любую подходящую по остальным ограничениям секцию, узкопроходную или
широкопроходную.

---

## Отбор секций

Прежде чем CP-SAT решает задачу Паллета → Секция, для каждой паллеты строится список
допустимых секций (`solver/cp_sat_model.py:76-86`, тождественная проверка также в
`optimizer/potential.py:71-93` и `optimizer/potential.py:96-125`):

```python
feasible[p.id] = []
current_sec_idx = section_idx.get(pallet_current_section.get(p.id))
for i, sec in enumerate(sections):
    if strict_narrow and p.is_narrow and not sec.narrow_aisle and i != current_sec_idx:
        continue  # узкопроходная паллета → только узкопроходные секции
    if (
        p.height <= sec.height
        and p.depth <= sec.depth
        and p.weight <= sec.max_lift_weight
        and p.width <= sec.eff_max_width
        and p.depth <= sec.eff_max_depth
    ):
        feasible[p.id].append(i)
```

`current_sec_idx` — текущая секция уже стоящей паллеты, она остаётся допустимой независимо
от правила узкопроходности (см. [Узкопроходные стеллажи](#узкопроходные-стеллажи)), иначе
существующая паллета выпадает из модели целиком.

Пять условий (плюс правило узкопроходности выше), **все обязательны**:

| # | Условие | Смысл |
|---|---------|-------|
| 1 | `pallet.height ≤ section.height` | высота секции (`typeSize_height`) |
| 2 | `pallet.depth ≤ section.depth` | глубина секции (`typeSize_depth`) |
| 3 | `pallet.weight ≤ section.max_lift_weight` | грузоподъёмность подъёма (`max_lift_weight`) |
| 4 | `pallet.width ≤ section.eff_max_width` | эффективный максимум ширины ОДНОЙ паллеты |
| 5 | `pallet.depth ≤ section.eff_max_depth` | эффективный максимум глубины ОДНОЙ паллеты |

`eff_max_width` / `eff_max_depth` — это **отдельные поля**, не производные от `gap_width`
(`models/section.py:59-67`):
```python
@property
def eff_max_width(self) -> float:
    # 1С уже резолвит 0 → ширина секции (CASE в SQL), но проверяем сами защитно.
    return self.max_width_pallet if self.max_width_pallet > 0 else self.width

@property
def eff_max_depth(self) -> float:
    # 1С НЕ резолвит этот fallback (в отличие от ширины) — применяем его сами.
    return self.max_depth_pallet if self.max_depth_pallet > 0 else self.depth
```

`max_width_pallet` берётся из `max_widthPallet` occupancy, `max_depth_pallet` — из
`max_depthPallet`. Это ограничение на **узкопроходные стеллажи**: максимальный размер
ОДНОЙ паллеты, а не сумма всех паллет в секции.

---

## Ограничения CP-SAT

После предфильтрации допустимых пар (паллета, секция), CP-SAT-модель
(`solver/cp_sat_model.py`) накладывает три дополнительных ограничения на **совокупность**
паллет в секции:

**Вместимость (строки 116-119):**
```python
model.Add(sum(vars_in_sec) <= sec.max_pallets)
```
Количество паллет в секции ≤ `max_pallets` (обычно 3, дефолт если ≤0 — тоже 3,
`models/occupancy_builder.py:54`: `max_pallets=row.max_pallets if row.max_pallets > 0 else 3`).

**Ширина с зазорами (строки 122-132):**
```python
count_var = sum(xv for _, xv in vars_in_sec)
width_sum = sum(int(p.width * SCALE) * xv for p, xv in vars_in_sec)
gap = int(sec.gap_width * SCALE)
width_limit = int(sec.width * SCALE)
model.Add(width_sum + count_var * gap + gap <= width_limit)
```
То есть: `SUM(pallet.width) + (N+1)*gap_width ≤ section.width`, где N — количество паллет
в секции после размещения. Эквивалентная формула в постпроцессинге
(`optimizer/potential.py:64-75`):
```python
free_width = section.width - SUM(existing_widths) - (N+1)*gap_width
# новая паллета входит, если:
free_width >= pallet.width + gap_width
```

**Вес секции (строки 135-141):**
```python
if math.isinf(sec.max_weight):
    continue  # unlimited_weight — пропускаем проверку
weight_sum = sum(int(p.weight * SCALE) * xv for p, xv in vars_in_sec)
model.Add(weight_sum <= int(sec.max_weight * SCALE))
```
`sec.max_weight` = `math.inf`, если `typeSize_unlimitedWeight=true` (`models/section.py:47-49`).

---

## Заблокированные паллеты

Паллета считается заблокированной (`movable=False`), если в occupancy `blockedN > 0` для
её слота (`models/occupancy_builder.py:69,87`: `is_blocked = blocked > 0`, `movable=not is_blocked`).

Заблокированные паллеты **всегда** зафиксированы на текущем месте — независимо от
`allowReslot` (`solver/cp_sat_model.py:144-155`):
```python
for p in self.existing_pallets:
    if p.movable:
        continue
    old_sec_idx = self.section_idx.get(self.pallet_current_section.get(p.id))
    model.Add(X[(p.id, old_sec_idx)] == 1)
    for si in feasible[p.id]:
        if si != old_sec_idx:
            model.Add(X[(p.id, si)] == 0)
```
Их адрес (`blocked=True` на `Address`) также не считается свободным для новых паллет
(`optimizer/section_optimizer.py`: фильтр `not a.blocked`).

---

## Реслот

Если `settings.allowReslot=false` (режим `place`) — движимые существующие паллеты **тоже**
не двигаются, фиксируются как заблокированные (`solver/cp_sat_model.py:176-187`).

Если `allowReslot=true` — вводится переменная `R[p]` = "паллета физически переехала в другую
секцию" (`cp_sat_model.py:100-104, 158-169`), и ограничивается их число
(`cp_sat_model.py:171-175`):
```python
max_by_percent = math.floor(current_pallet_count * settings.maxReslotPercent / 100)
model.Add(sum(R.values()) <= max_by_percent)
```
где `current_pallet_count` = количество движимых существующих паллет (не всех, не новых).

В режиме `compact` (`optimizer/global_optimizer.py:47`) реслот **всегда** включён
принудительно: `allow_reslot = True if req.mode == "compact" else settings.allowReslot`
— это единственная цель этого режима (уплотнение склада, новых паллет не бывает).

---

## Лимит операций

`cp_sat_model.py:189-197`:
```python
new_placed_sum = SUM(X[паллета, секция] для всех новых паллет)
move_sum = SUM(R.values())
model.Add(new_placed_sum + move_sum <= settings.maxOperations)
```
Суммарно PUT (новые) + MOVE (переставленные) не может превышать `maxOperations` за один
запуск оптимизации.

---

## Warm Start

`solver/warm_start.py` — эвристика First Fit Decreasing, результат передаётся в CP-SAT как
`AddHint` (не жёсткое ограничение, только подсказка для ускорения поиска):

1. Заблокированные/недвижимые паллеты — сразу фиксируются в своей текущей секции.
2. Остальные (новые + движимые существующие, если `allow_reslot`) сортируются по **убыванию
   ширины** (`sorted(to_place, key=lambda p: p.width, reverse=True)`).
3. Секции сортируются по **приоритету узкопроходности, затем убыванию ширины**
   (`sorted(sections, key=lambda s: (not s.narrow_aisle, -s.width))`) — узкопроходные секции
   всегда проверяются первыми (см. [Узкопроходные стеллажи](#узкопроходные-стеллажи)).
4. Для каждой паллеты — первая секция (в этом порядке), которая проходит
   `section_fits_pallet()` (та же проверка ширины+зазоров+веса+количества+узкопроходности,
   что и `optimizer/potential.py:96-128`, с учётом `strict_narrow`).

---

## Выбор адреса

После того как CP-SAT определил Паллета → Секция, `optimizer/section_optimizer.py`
выбирает Паллета → Адрес **в рамках этой уже назначенной секции**. Правило — точная копия
условия из 1С (`Лико_СкладскиеСекции`), без scoring:

```
ВЫБОР
    КОГДА ШиринаПаллета > Типоразмер.Ширина * 2 / 3
        ТОГДА Адрес2
    КОГДА Паллет1 = ПустаяСсылка
        ТОГДА Адрес1
    КОГДА Паллет3 = ПустаяСсылка
        ТОГДА Адрес3
    КОГДА Паллет2 = ПустаяСсылка
        ТОГДА Адрес2
    ИНАЧЕ
        Не ставим
КОНЕЦ
```

Реализация (`optimizer/section_optimizer.py:63-102`):

1. Если `pallet.width > section.width * 2/3` → пробуем Адрес2 (position=2). Свободен и не
   `blocked` → ставим туда. Иначе — `None` (в эту секцию большая паллета не ставится вообще,
   даже если Адрес1/Адрес3 свободны).
2. Иначе — Адрес1 свободен и не `blocked` → ставим туда.
3. Иначе — Адрес3 свободен и не `blocked` → ставим туда.
4. Иначе — Адрес2 свободен и не `blocked` → ставим туда.
5. Иначе — `None` (нет свободного адреса в секции по этому правилу).

Порядок проверки — **всегда** 2 (для большой) или 1→3→2 (для обычной). Никакого сравнения
по объёму, потенциалу или другим метрикам между адресами — первый подходящий по этому
порядку и есть выбранный.

---

## Причины отказа

Если новую паллету не удалось разместить, причина вычисляется в
`optimizer/global_optimizer.py:_determine_not_placed_reason` (строки 242-291) — проверка
по всем секциям, приоритет в этом порядке:

| Reason | Условие |
|---|---|
| `RESLOT_LIMIT` | Хотя бы одна секция подошла по всем физическим ограничениям, но паллета всё равно не разместилась (упёрлись в лимит реслота/операций/решатель не успел) |
| `NARROW_AISLE_MISMATCH` | Паллета узкопроходная (`width ≤ 1200` И `depth ≤ 1200`), все проверенные секции — `narrowAisle=false`, а `settings.strictNarrowAislePlacement=true` (default) |
| `HEIGHT_LIMIT` | `pallet.height > section.height` во всех проверенных секциях |
| `DEPTH_LIMIT` | `pallet.depth > section.depth` |
| `LIFT_LIMIT` | `pallet.weight > section.max_lift_weight` |
| `MAX_PALLET_SIZE_LIMIT` | `pallet.width > eff_max_width` или `pallet.depth > eff_max_depth` |
| `WEIGHT_LIMIT` | суммарный вес секции с этой паллетой превысил `max_weight` |
| `NO_SPACE` | не прошла `section_fits_pallet` (ширина+зазоры или количество) — дефолт, если ничего из выше не подошло |

`details` содержит `{"checkedSections": N, "availableSections": M}`.

---

## Целевая функция

**В самом CP-SAT** (`solver/cp_sat_model.py`) целевая функция упрощённая —
потенциал считать в решателе дорого, поэтому:
```python
objective = 100000 * placed_sum - 1000 * section_move_sum + 10 * narrow_priority_sum
```
Максимизируется количество размещённых паллет, минимизируется количество перемещений секций,
небольшой бонус за размещение узкопроходной паллеты в узкопроходную секцию
(`narrow_priority_sum` — см. [Узкопроходные стеллажи](#узкопроходные-стеллажи)).

**В финальном ответе** (`optimizer/scoring.py`, коэффициенты из `config/weights.json`,
захардкоживание запрещено комментарием в файле) считается более полный `GlobalScore`,
но это **постпроцессинг для метрики в ответе API**, не то, что оптимизирует сам решатель:
```python
GlobalScore = placedPalletWeight * placed
            - sectionMovePenalty * section_moves
            - addressMovePenalty * address_moves       # всегда 0 — не считается отдельно
            - potentialLossPenalty * potential_loss
            - spaceLossPenalty * unused_space           # всегда 0 — не считается
            - sectionUsagePenalty * used_sections
```
`address_moves` и `unused_space` в `global_optimizer.py:192-199` всегда передаются `0` —
это компоненты формулы, которые сейчас нигде не вычисляются.

`compute_address_score`/`AddressScoreComponents` (в `optimizer/scoring.py`) больше **не
используются** нигде в основном коде (только в `tests/test_scoring.py`) — это остаток от
прежней (неверной) реализации выбора адреса через scoring, замененной на точное правило 1С
выше.

---

## Валидация запроса

`validation/validator.py` проверяет **только ссылочную целостность**, не бизнес-правила:

1. Нет дублирующихся `section_id` в `occupancy`.
2. Нет дублирующихся `address_id` (собранных из `address1/2/3` всех секций).
3. Нет дублирующихся `pallet_id` (из `pallet1/2/3_id` всех секций).
4. Нет дублирующихся `id` в `newPallets`.
5. `newPallets` не содержит `id`, уже занятый существующей паллетой в occupancy.

Структурные проверки (обязательность полей, `width/height/depth > 0`, `weight ≥ 0`,
`gap_width ≥ 0`, диапазоны `maxReslotPercent` 0-100 и т.п.) выполняет Pydantic на этапе
разбора `OptimizationRequest` (`api/schemas.py`) — это стандартные `Field(..., gt=0)` и
подобные ограничения, а не отдельная бизнес-логика.

**Нет** проверки `accessLevel in [1,2,3]`, **нет** проверки "секция не restricted" на этапе
валидации — `restricted` обрабатывается позже, при построении модели (см. раздел
[Что используется](#что-используется)).
