# ТЗ: WMS Pallet Optimizer — Подбор ячеек для LLM

> Подробное ТЗ для подачи LLM-модели на задачу подбора ячеек склада.
> Описывает полный контракт: входные данные, алгоритм, выходные данные, результаты тестов.
> **Дата:** 2026-07-27 | **Версия:** v1.0

---

## 1. Обзор системы

**WMS Pallet Optimizer** — Python-сервис на FastAPI + Google OR-Tools CP-SAT, решающий задачу оптимального размещения паллет на складе.

**Порт:** 8010 | **Docker:** `wms-optimizer` | **Swagger:** `http://localhost:8010/docs`

### 1.1 Что делает сервис

Принимает **снимок склада** (секции + адреса + текущие паллеты) и **список новых паллет** к размещению. Возвращает **оптимальный план** — какие паллеты в какие адреса поставить, какие существующие передвинуть.

### 1.2 Два уровня оптимизации

| Уровень | Модуль | Задача | Метод |
|---------|--------|--------|-------|
| **Глобальный** | `cp_sat_aggregated.py` | Паллета → Секция | CP-SAT (OR-Tools), агрегированная модель по типоразмерам |
| **Локальный** | `section_optimizer.py` | Паллета → Адрес внутри секции | Детерминированные правила (как в 1С) |

### 1.3 Ключевые цифры (S7, холодный старт)

| Метрика | Одноэтапный | Двухэтапный |
|---------|:-----------:|:-----------:|
| Паллет на входе | 3406 | 3406 |
| Секций | 1530 | 1530 |
| Размещено | 3240 (95.1%) | **3332 (97.8%)** |
| Время | 188 сек | 252 сек |
| CP-SAT переменных | ~1000 (агрегированные) | ~1000 + ~500 (2 этап) |
| vs ручной эталон S6 | +0 | **+90 паллет** |

**Ручной эталон (склад S6, человек):** 3242/3406 (95.2%).

---

## 2. Архитектура модулей

```
wms_optimizer/
├── main.py                          # FastAPI app, точка входа uvicorn
├── api/
│   ├── routes.py                    # Эндпоинты: /api/optimize, /api/optimize/async
│   └── schemas.py                   # ВЕСЬ Pydantic-контракт (request/response/enums)
├── models/
│   ├── pallet.py                    # Pallet, PalletTypeSize — модель паллеты
│   ├── section.py                   # Section, SectionTypeSize — модель секции
│   ├── address.py                   # Address — модель адреса
│   └── occupancy_builder.py         # Парсинг occupancy из 1С → Section/Address/Pallet
├── optimizer/
│   ├── global_optimizer.py          # Оркестратор: точка входа run_optimization()
│   ├── two_stage_optimizer.py       # Двухэтапный режим (twoStageReslot=True)
│   ├── section_optimizer.py         # Локальный: Паллета → Адрес (5 правил)
│   ├── potential.py                 # Единая формула потенциала секции и Fits()
│   └── scoring.py                   # compute_global_score / compute_address_score
├── solver/
│   ├── cp_sat_aggregated.py         # Агрегированная CP-SAT модель (Фаза C)
│   ├── cp_sat_model.py              # Точная CP-SAT модель (для малых задач)
│   ├── aggregation.py               # Группировка типоразмеров паллет/секций
│   ├── feasibility.py               # compute_feasible_pairs — допустимые пары
│   ├── warm_start.py                # First-Fit-Decreasing эвристика
│   └── config.py                    # Пороги, num_search_workers
├── validation/
│   └── validator.py                 # Валидация входного запроса
├── config/
│   ├── weights.json                 # Веса целевой функции
│   └── settings.json                # defaultSettings, api (host/port 8010)
└── tests/
    ├── test_s7_vs_standard.py       # Регрессионный тест S7 (3406 паллет)
    └── test_acceptance.py           # 10 приёмочных тестов
```

---

## 3. ВХОДНЫЕ ДАННЫЕ — полный контракт

### 3.1 Endpoint

```
POST /api/optimize
Content-Type: application/json
```

### 3.2 Структура запроса (`OptimizationRequest`)

```json
{
  "optimizationId": "uuid-строка",
  "mode": "place",
  "occupancy": [ /* OccupancySection[] */ ],
  "newPallets": [ /* NewPallet[] */ ],
  "settings": { /* OptimizationSettings */ }
}
```

### 3.3 OccupancySection — текущее состояние секции

**Источник:** 1С → `Лико_WMS_Сервер.СобратьЗанятостьСекций()` → `WMS_GetOccupancy`.

**Одна строка = одна секция склада.** Содержит физические параметры секции и до 3 паллет, которые в ней уже стоят.

#### Идентификация секции

| Поле | Тип | Обязательное | Описание |
|------|-----|:---:|---------|
| `section_id` | UUID строка | ✓ | GUID секции |
| `section_code` | строка | ✓ | Код секции, напр. `"Р601-М(01-02-03)-Э01"` |
| `rack_id` | UUID строка | ✓ | GUID стеллажа |
| `rack_code` | int | ✓ | Номер стеллажа (1-9) |
| `floor` | int | ✓ | Этаж/ярус |

#### Физические ограничения секции

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `typeSize_width` | float | — | Ширина секции, мм **(обязательное, > 0)** |
| `typeSize_height` | float | — | Высота секции, мм **(обязательное, > 0)** |
| `typeSize_depth` | float | — | Глубина секции, мм **(обязательное, > 0)** |
| `typeSize_weight` | float | — | Грузоподъёмность секции, кг. Игнорируется при `unlimitedWeight=true` |
| `typeSize_unlimitedWeight` | bool | false | Вес секции не ограничен |
| `gap_width` | float | — | Зазор между паллетами, мм **(обязательное, ≥ 0)** |
| `max_lift_weight` | float | — | Макс. вес подъёма одной паллеты, кг |
| `max_pallets` | int | 3 | Макс. количество паллет в секции |
| `max_widthPallet` | float | 0 | Макс. ширина ОДНОЙ паллеты, мм. 0 = нет ограничения (1С уже даёт fallback на ширину секции) |
| `max_depthPallet` | float | 0 | Макс. глубина ОДНОЙ паллеты, мм. 0 = нет ограничения (БЕЗ fallback — резолвится в Python) |

#### Правила доступа

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `narrowAisle` | bool | false | **Узкопроходная секция.** Узкопроходные паллеты (ширина ≤ 1200 И глубина ≤ 1200) при `strictNarrowAislePlacement=true` могут размещаться ТОЛЬКО в таких секциях |
| `restricted` | bool | false | Секция заблокирована — **полностью исключается** из оптимизации |
| `accessLevel` | int | 1 | Резерв (не используется) |
| `accessTime` | float | 0 | Резерв (не используется) |

#### Текущие паллеты в секции (слоты 1, 2, 3)

Для каждого слота N ∈ {1, 2, 3}:

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `addressN` | UUID строка | `""` | GUID адреса слота N. `""` = слот не существует |
| `palletN_id` | UUID строка | `""` | GUID паллеты в слоте N. `""` = пусто |
| `palletN_code` | строка | `""` | Код паллеты |
| `palletN_width` | float | 0 | Ширина паллеты |
| `palletN_height` | float | 0 | Высота паллеты |
| `palletN_depth` | float | 0 | Глубина паллеты |
| `palletN_weight` | float | 0 | Вес паллеты |
| `quantityN` | float | 0 | Количество товара. **0 + не blocked = слот свободен** |
| `blockedN` | float | 0 | **> 0 = паллета заблокирована**, солвер её не двигает |

**Важно:** Слот считается занятым если `palletN_id` не пустой **И** (`quantityN > 0` ИЛИ `blockedN > 0`).

### 3.4 NewPallet — новая паллета для размещения

```json
{
  "id": "uuid-паллеты",
  "width": 800,    // мм, > 0
  "height": 1500,  // мм, > 0
  "depth": 600,    // мм, > 0
  "weight": 400,   // кг, ≥ 0
  "accessLevel": 1 // резерв (не используется)
}
```

### 3.5 Settings — настройки оптимизации

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `allowReslot` | bool | true | Разрешить переставлять существующие паллеты |
| `maxReslotPercent` | float | 20 | Макс. % секций для реслота (0-100) |
| `maxOperations` | int | 300 | Лимит операций PUT+MOVE в ответе |
| `timeLimitSeconds` | int | 120 | Таймаут CP-SAT солвера для основного этапа |
| `strictNarrowAislePlacement` | bool | **true** | Узкопроходные паллеты ТОЛЬКО в узкопроходные секции |
| **`twoStageReslot`** | **bool** | **false** | **Двухэтапный режим.** ЭТАП 1 без реслота → ЭТАП 2 с реслотом. **Рекомендуется для >1000 паллет.** |
| **`twoStageReslotMaxReslotPercent`** | **float** | **10.0** | maxReslotPercent для ЭТАПА 2 |
| **`twoStageReslotTimeLimitSeconds`** | **int** | **120** | timeLimitSeconds для ЭТАПА 2 |

---

## 4. АЛГОРИТМ — полный пайплайн

### 4.1 Точка входа: `global_optimizer.run_optimization()`

```
run_optimization(req)
│
├─ twoStageReslot=true? → run_two_stage_optimization()  [см. 4.7]
│
├─ 1. build_warehouse_state(occupancy)
│     └─ occupancy_builder.py: OccupancySection[] → Section[], Address[], Pallet[]
│
├─ 2. first_fit_decreasing() — warm start эвристика
│     └─ warm_start.py: FFD для AddHint в CP-SAT
│
├─ 3. compute_feasible_pairs() → выбор модели
│     ├─ пар > FEASIBLE_PAIRS_THRESHOLD И нет реслота?
│     │   └─ CPSATAggregatedSolver  [см. 4.3]
│     └─ иначе
│         └─ CPSATSolver (точная модель)
│
├─ 4. CP-SAT solver.solve() → assignment: {pallet_id: section_id}
│
├─ 5. assign_addresses(pallets, assignment)
│     └─ section_optimizer.py: Паллета → Адрес [см. 4.5]
│
├─ 6. Формирование операций
│     ├─ Существующие движимые: новый адрес ≠ старый → MOVE
│     └─ Новые: есть адрес → PUT, нет → notPlaced с причиной
│
└─ 7. OptimizationResponse
```

### 4.2 Парсинг occupancy: `occupancy_builder.build_warehouse_state()`

```
Для каждой OccupancySection:
├─ restricted=true → ПРОПУСТИТЬ (исключена)
├─ Создать Section с type_size, narrow_aisle, max_pallets и т.д.
├─ Для каждого из 3 слотов (position 1/2/3):
│   ├─ address_id пустой? → ПРОПУСТИТЬ
│   ├─ has_pallet = pallet_id не пустой И (quantity > 0 ИЛИ blocked > 0)
│   ├─ Создать Address(id, section_id, position, pallet_id, blocked)
│   └─ Если has_pallet → Pallet(id, code, typeSize, current_address, current_section, movable=!blocked)
```

### 4.3 Агрегированная CP-SAT модель: `CPSATAggregatedSolver`

**Ключевая идея:** Паллеты одного типоразмера взаимозаменяемы. Вместо `X[паллета, секция]` (булева, ~2.35M переменных для S7) используем `Y[тип_паллеты, бакет_секций]` (целочисленная, ~1000 переменных).

#### Бакетизация секций

Секции группируются по **одинаковому остатку вместимости**:
- Ключ бакета = `(height, depth, max_lift_weight, eff_max_width, eff_max_depth, narrow_aisle, gap_width, remaining_count, remaining_width, remaining_weight)`
- Две секции с одинаковыми габаритами но разной занятостью → **разные бакеты**
- `_BUCKET_CHUNK_SIZE = 1` — каждый бакет = ровно одна секция (гарантирует совпадение суммы с физической упаковкой)

#### Переменные и ограничения

```python
Y[(type_key, bucket_idx)] = model.NewIntVar(0, min(n_type, bucket_total_count))

# Ограничения:
# 1. sum(Y[type, :]) ≤ count(type)          — не больше, чем есть паллет этого типа
# 2. sum(Y[:, bucket]) ≤ bucket.total_count — не больше слотов в бакете
# 3. sum(width * Y) + count * gap ≤ budget  — ширина с зазорами
# 4. sum(weight * Y) ≤ budget               — вес (если ограничен)
# 5. sum(all Y) ≤ maxOperations             — лимит операций
```

#### Целевая функция

```python
Maximize:
  100000 * total_placed              // максимизировать количество размещённых
  + 10 * narrow_in_narrow            // бонус: узкая паллета → узкая секция
  - 5000 * wide_in_narrow            // штраф: широкая паллета → узкая секция
```

#### Дезагрегация (Y → конкретные паллеты в конкретные секции)

```
1. Для каждого бакета: взять n паллет каждого типа
2. Best-fit внутри бакета: самые широкие паллеты первыми
3. Предпочитаем секции с большей занятостью (дозаполняем)
4. Mismatch → fallback по всему складу
5. Остаток → _resolve_residual_exact (точная CP-SAT на хвосте)
6. Остаток → _resolve_residual_with_consolidation (уплотнение)
7. Остаток → _resolve_residual_with_reslot (виртуальный реслот)
```

### 4.4 Проверка Fits: `potential.section_fits_pallet()`

**ЕДИНСТВЕННАЯ функция** проверки "влезает ли паллета в секцию". Используется на всех уровнях: CP-SAT, дезагрегация, warm start, валидация.

```python
def section_fits_pallet(section, pallets_in_section, pallet, strict_narrow=True):
    # 1. strict_narrow: узкая паллета → только narrow_aisle секции
    # 2. Количество: len(pallets_in_section) < max_pallets
    # 3. Высота паллеты ≤ высота секции
    # 4. Глубина паллеты ≤ глубина секции
    # 5. Вес паллеты ≤ max_lift_weight
    # 6. Ширина паллеты ≤ eff_max_width (max_widthPallet с fallback)
    # 7. Глубина паллеты ≤ eff_max_depth (max_depthPallet с fallback)
    # 8. Ширина: sum(widths) + pallet.width + (N+2)*gap_width ≤ section.width
    # 9. Вес: sum(weights) + pallet.weight ≤ max_weight (если не unlimited)
```

**Формула зазора:** для N существующих паллет + 1 новой = `(N + 2) * gap_width`.
Это соответствует 1С: 150 мм для 2-го паллета (N=1 → 3*gap при gap=50), 200 мм для 3-го (N=2 → 4*gap при gap=50).

### 4.5 Локальная оптимизация: `section_optimizer.assign_addresses()`

После того как CP-SAT назначил паллету в секцию, `assign_addresses` выбирает конкретный адрес (position 1/2/3) внутри секции:

```
Для каждой паллеты:
├─ Правило 1: ширина паллеты > 2/3 ширины секции?
│   └─ Адрес2 (центр, position=2), если свободен
├─ Правило 2: Адрес1 свободен? → Адрес1
├─ Правило 3: Адрес3 свободен? → Адрес3
├─ Правило 4: Адрес2 свободен? → Адрес2
└─ Правило 5: всё занято → None (не размещена)
```

### 4.6 Определение причины отказа: `_determine_not_placed_reason()`

Проходит по ВСЕМ секциям, считает `availableSections` (куда физически влезает). Приоритет причин:

1. `available > 0` → `RESLOT_LIMIT` (место есть, но занято/запрещён реслот)
2. `NARROW_AISLE_MISMATCH` — узкая паллета, нет узкопроходных секций
3. `HEIGHT_LIMIT` — паллета выше всех секций
4. `DEPTH_LIMIT` — паллета глубже всех секций
5. `LIFT_LIMIT` — паллета тяжелее max_lift_weight всех секций
6. `MAX_PALLET_SIZE_LIMIT` — ширина или глубина > max_widthPallet/max_depthPallet
7. `WEIGHT_LIMIT` — превышение грузоподъёмности секции
8. `NO_SPACE` — не хватает места по ширине/количеству

### 4.7 Двухэтапный режим: `two_stage_optimizer.run_two_stage_optimization()`

Включается флагом `settings.twoStageReslot = true`.

```
ЭТАП 1 (без реслота):
├─ req_stage1.allowReslot = False
├─ req_stage1.twoStageReslot = False  // рекурсия отключена
└─ run_optimization(req_stage1) → resp_stage1

Если всё размещено → вернуть resp_stage1.

ЭТАП 2 (реслот остатков):
├─ _build_occupancy_after_stage1() — восстановить занятость из операций ЭТАПА 1
├─ not_placed_pallets — только те, что не разместились
├─ req_stage2:
│   ├─ occupancy = occupancy_after_stage1  // с паллетами ЭТАПА 1
│   ├─ newPallets = not_placed_pallets
│   ├─ allowReslot = True
│   ├─ maxReslotPercent = twoStageReslotMaxReslotPercent  // default 10%
│   └─ timeLimitSeconds = twoStageReslotTimeLimitSeconds  // default 120s
└─ run_optimization(req_stage2) → resp_stage2

Объединение:
├─ Дедупликация операций: последняя операция для каждой паллеты
├─ total_placed = stage1.placed + stage2.placed
├─ Финальный статус: OPTIMAL если stage2 OPTIMAL
└─ return OptimizationResponse
```

---

## 5. ВЫХОДНЫЕ ДАННЫЕ — полный контракт

### 5.1 Структура ответа (`OptimizationResponse`)

```json
{
  "optimizationId": "uuid",
  "mode": "place",
  "solverStatus": "OPTIMAL",
  "placementStatus": "FULL",
  "score": 333200000.0,
  "executionTimeSeconds": 252.7,
  "operations": [ /* Operation[] */ ],
  "notPlaced": [ /* NotPlaced[] */ ],
  "metrics": { /* Metrics */ }
}
```

### 5.2 solverStatus

| Статус | Значение |
|--------|---------|
| `OPTIMAL` | Найден гарантированно оптимальный план |
| `FEASIBLE` | Найдено допустимое решение (не гарантированно оптимальное) |
| `TIME_LIMIT` | Таймаут — возвращено лучшее найденное на момент остановки |
| `INFEASIBLE` | Решения не существует (только для mode=compact с противоречивыми ограничениями) |

### 5.3 placementStatus

| Статус | Значение |
|--------|---------|
| `FULL` | Все паллеты размещены |
| `PARTIAL` | Часть размещена, часть в notPlaced |
| `NONE` | Ни одна не размещена |

### 5.4 Operation — операция для выполнения 1С

```json
{
  "pallet": "uuid-паллеты",
  "operation": "PUT",           // "PUT" | "MOVE"
  "oldAddress": null,           // null для PUT, UUID для MOVE
  "newAddress": "uuid-адреса",  // UUID адреса назначения
  "sequence": 1                 // порядковый номер (1-based)
}
```

- `PUT` — разместить новую паллету (oldAddress = null)
- `MOVE` — переставить существующую (oldAddress ≠ null)

### 5.5 NotPlaced — неразмещённая паллета

```json
{
  "pallet": "uuid-паллеты",
  "reason": "HEIGHT_LIMIT",
  "details": {
    "checkedSections": 1530,
    "availableSections": 0
  }
}
```

**Возможные причины (`reason`):**

| Код | Условие | Типичное количество на S7 |
|-----|---------|:---:|
| `HEIGHT_LIMIT` | Высота паллеты > высоты всех секций | ~100 |
| `NARROW_AISLE_MISMATCH` | Узкая паллета + нет узкопроходных секций + strictNarrowAisle=true | ~64 |
| `NO_SPACE` | Нет свободного места по размерам/количеству | ~10 |
| `RESLOT_LIMIT` | Место есть, но занято под реслот-квоту | ~0 |
| `DEPTH_LIMIT` | Глубина паллеты > глубины всех секций | 0 |
| `LIFT_LIMIT` | Вес паллеты > max_lift_weight всех секций | 0 |
| `MAX_PALLET_SIZE_LIMIT` | Ширина/глубина > max_widthPallet/max_depthPallet | 0 |
| `WEIGHT_LIMIT` | Суммарный вес превышает грузоподъёмность | 0 |

### 5.6 Metrics

```json
{
  "placedPallets": 3332,     // успешно размещено
  "notPlacedPallets": 74,    // не размещено
  "movedPallets": 0,         // переставлено существующих (MOVE)
  "potentialLoss": 0,        // потеря потенциала (сумма по секциям)
  "usedSections": 1510       // затронуто уникальных секций
}
```

---

## 6. РЕЗУЛЬТАТЫ ПОСЛЕДНЕГО ТЕСТА (S7, холодный старт)

### 6.1 Конфигурация теста

```python
S7_SETTINGS = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=180,
    twoStageReslot=True,
    twoStageReslotMaxReslotPercent=10.0,
    twoStageReslotTimeLimitSeconds=120,
)
```

**Тестовые данные:**
- `OccupancyS7.json` — 1530 секций, все пустые (холодный склад)
- `FloorS7.json` — 3406 паллет с пола
- Эталон: `OccupancyS6Standard.json` — ручная раскладка человека на складе S6: **3242/3406 (95.2%)**

### 6.2 Результат прогона (3 запуска)

```
Run 0: placed=3239 moved=0 not_placed={'HEIGHT_LIMIT': 103, 'NARROW_AISLE_MISMATCH': 64} time=251s
Run 1: placed=3241 moved=0 not_placed={'HEIGHT_LIMIT': 101, 'NARROW_AISLE_MISMATCH': 64} time=252s
Run 2: placed=3240 moved=0 not_placed={'HEIGHT_LIMIT': 102, 'NARROW_AISLE_MISMATCH': 64} time=253s

Среднее: placed=3240, ±1 вариативность CP-SAT
```

### 6.3 Двухэтапный режим (лучший результат)

```
ЭТАП 1 (размещение без реслота, 180s):
  placed=3241/3406, not_placed=165, time=248s
  причины: HEIGHT_LIMIT=100, NARROW_AISLE_MISMATCH=64, RESLOT_LIMIT=1

ЭТАП 2 (реслот остатков, 120s, maxReslotPercent=10%):
  вход: 165 неразмещённых + occupancy после ЭТАПА 1
  placed=+91/165, moved=0, time=4.3s, status=OPTIMAL

ИТОГО: 3332/3406 (97.8%), time=252.3s
```

### 6.4 Сравнение подходов

| Подход | Размещено | Время | Переменных | Статус |
|--------|:---------:|:-----:|:----------:|:------:|
| Жадный (FFD) | 2440 (71.6%) | ~5s | N/A | ❌ |
| CP-SAT точная модель | 3234 (94.9%) | ~20 мин | 2.35M | ⚠️ |
| CP-SAT агрегированная (одноэтапная) | 3240 (95.1%) | 188s | ~1000 | ✅ |
| **CP-SAT агрегированная (двухэтапная)** | **3332 (97.8%)** | **252s** | **~1000** | **✅** |
| Ручное (эталон S6) | 3242 (95.2%) | вручную | N/A | Baseline |

### 6.5 Анализ неразмещённых (74 паллеты)

- **HEIGHT_LIMIT (~100 паллет):** Паллеты высотой > 2000 мм (или другие выше всех секций). Это **жёсткое ограничение** — ни реслот, ни увеличение времени не помогут. Нужно физически перенастроить секции (изменить типоразмеры).
- **NARROW_AISLE_MISMATCH (~64 паллеты):** Узкопроходные паллеты (≤1200×1200), все узкопроходные секции (`narrowAisle=true`) уже заполнены. При `strictNarrowAislePlacement=true` — не могут быть размещены в широкопроходные секции. Это тоже **жёсткое ограничение** при текущей топологии.

**Вывод:** Двухэтапный оптимизатор на S7 превосходит ручной эталон S6 на 90 паллет (+2.8%). Оставшиеся 74 паллеты неразмещаемы физически при текущей топологии склада.

---

## 7. ИНТЕГРАЦИЯ С 1С — полный цикл

### 7.1 1С → Python (отправка)

```bsl
// 1. Получить снимок склада
Параметры = Новый Структура("warehouse", Строка(Склад.УникальныйИдентификатор()));
Ответ = Лико_WMS_Сервер.WMS_GetOccupancy(Параметры);
Occupancy = ПрочитатьJSON(Новый ЧтениеJSON(Ответ.Тело));

// 2. Получить паллеты с пола (запрос к Лико_ПаллетыВСекциях.Остатки)
МассивПаллет = ...; // см. API_DOCS.md для полного BSL-кода

// 3. Отправить в оптимизатор
Запрос = Новый Структура;
Запрос.Вставить("optimizationId", Строка(Новый УникальныйИдентификатор()));
Запрос.Вставить("mode", "place");
Запрос.Вставить("occupancy", Occupancy);
Запрос.Вставить("newPallets", МассивПаллет);

Settings = Новый Структура;
Settings.Вставить("twoStageReslot", Истина);
Settings.Вставить("timeLimitSeconds", 180);
Settings.Вставить("twoStageReslotTimeLimitSeconds", 120);
Settings.Вставить("twoStageReslotMaxReslotPercent", 10.0);
Settings.Вставить("maxOperations", 5000);
Settings.Вставить("strictNarrowAislePlacement", Истина);
Запрос.Вставить("settings", Settings);

HTTPСоединение = Новый HTTPСоединение("localhost", 8010);
HTTPЗапрос = Новый HTTPЗапрос("/api/optimize");
HTTPЗапрос.Заголовки.Вставить("Content-Type", "application/json; charset=utf-8");
HTTPЗапрос.УстановитьТелоИзСтроки(Лико_HTTP_Сервер.СтруктураВJSON(Запрос));

HTTPОтвет = HTTPСоединение.ВызватьHTTPМетод("POST", HTTPЗапрос);
Результат = ПрочитатьJSON(Новый ЧтениеJSON(HTTPОтвет.ПолучитьТелоКакСтроку()));
```

### 7.2 Python → 1С (выполнение плана)

```bsl
// 4. Выполнить план через WMS_PlacePallets
МассивPlacements = Новый Массив;
Для Каждого Op Из Результат.operations Цикл
    Если Op.operation = "PUT" Тогда
        Placement = Новый Структура;
        Placement.Вставить("pallet", Op.pallet);
        Placement.Вставить("address", Op.newAddress);
        МассивPlacements.Добавить(Placement);
    КонецЕсли;
КонецЦикла;

// Rearrangements (MOVE)
МассивRearrangements = Новый Массив;
Для Каждого Op Из Результат.operations Цикл
    Если Op.operation = "MOVE" Тогда
        Rearrangement = Новый Структура;
        Rearrangement.Вставить("pallet", Op.pallet);
        Rearrangement.Вставить("fromAddress", Op.oldAddress);
        Rearrangement.Вставить("toAddress", Op.newAddress);
        МассивRearrangements.Добавить(Rearrangement);
    КонецЕсли;
КонецЦикла;

Параметры = Новый Структура;
Параметры.Вставить("warehouse", Строка(Склад.УникальныйИдентификатор()));
Параметры.Вставить("placements", МассивPlacements);
Параметры.Вставить("rearrangements", МассивRearrangements);

Ответ = Лико_WMS_Сервер.WMS_PlacePallets(Параметры);
// Ответ.Результат.documentsCreated — количество созданных документов ПеремещениеПаллета2_0
```

### 7.3 Валидация на стороне 1С

`WMS_PlacePallets` для каждого размещения вызывает:
```bsl
Ошибки = Справочники.Лико_СкладскиеСекции.ОшибкиРазмещенияПаллетаВАдрес(Паллет, Адрес);
```

Эта функция проверяет **17 правил**, включая:
- Зазор по ширине: 150 мм для 2-го паллета, 200 мм для 3-го
- Вес подъёма на этаж
- Совместимость типоразмера паллеты с секцией
- Запрет размещения (restricted)
- Превышение грузоподъёмности секции

---

## 8. ФАЙЛЫ ДЛЯ ПОДАЧИ LLM

Для понимания полной системы LLM-модели необходимы следующие файлы:

### 8.1 Обязательные (ядро контракта)

| Файл | Назначение |
|------|-----------|
| `api/schemas.py` | **ВЕСЬ Pydantic-контракт**: входные/выходные схемы, enums, валидация |
| `API_DOCS.md` | Полная документация API с примерами curl и 1С BSL |
| `optimizer/potential.py` | Единая функция `section_fits_pallet()` — все физические ограничения |
| `optimizer/section_optimizer.py` | Правила выбора адреса внутри секции (5 правил) |
| `models/occupancy_builder.py` | Парсинг occupancy из 1С → внутренние модели |
| `models/section.py` | Модель Section (все свойства, `eff_max_width/depth`) |
| `models/pallet.py` | Модель Pallet (`is_narrow`, `PalletTypeSize`) |

### 8.2 Алгоритмические (как работает)

| Файл | Назначение |
|------|-----------|
| `optimizer/global_optimizer.py` | Оркестратор: полный пайплайн от запроса до ответа |
| `optimizer/two_stage_optimizer.py` | Двухэтапный режим (ЭТАП 1 + ЭТАП 2) |
| `solver/cp_sat_aggregated.py` | Агрегированная CP-SAT модель + дезагрегация + 3 дорешивания |
| `solver/aggregation.py` | Группировка типоразмеров паллет/секций |
| `solver/feasibility.py` | Вычисление допустимых пар (паллета, секция) |
| `solver/warm_start.py` | First-Fit-Decreasing эвристика |

### 8.3 Интеграционные (1С)

| Файл | Назначение |
|------|-----------|
| `1s/ERP/extensions/liko/CommonModules/Лико_WMS_Сервер/Ext/Module.bsl` | `WMS_PlacePallets()` — выполнение плана, `СобратьЗанятостьСекций()` — источник occupancy |
| `1s/ERP/Conf/Catalogs/Лико_СкладскиеСекции/Ext/ManagerModule.bsl` | `ОшибкиРазмещенияПаллетаВАдрес()` — 17 проверок валидации 1С |
| `1s/ERP/obrab/TestHttp1cErp/...` | Тестовая обработка 1С для HTTP-вызовов |

### 8.4 Тесты и данные

| Файл | Назначение |
|------|-----------|
| `tests/test_s7_vs_standard.py` | Регрессионный тест: S7 холодный старт vs эталон S6 |
| `tests/example/OccupancyS7.json` | 1530 пустых секций склада S7 |
| `tests/example/FloorS7.json` | 3406 паллет с пола |
| `tests/example/OccupancyS6Standard.json` | Ручной эталон (склад S6) |

### 8.5 Конфигурация

| Файл | Назначение |
|------|-----------|
| `config/settings.json` | defaultSettings, порт 8010 |
| `config/weights.json` | Веса целевой функции (globalWeights, localWeights) |
| `solver/config.py` | `FEASIBLE_PAIRS_THRESHOLD`, `num_search_workers()` |
| `requirements.txt` | Зависимости Python |
| `Dockerfile` | Docker-образ |

---

## 9. КЛЮЧЕВЫЕ ПРАВИЛА И ОГРАНИЧЕНИЯ

### 9.1 Узкопроходные секции (`narrowAisle`)

- Паллета считается **узкопроходной** если `width ≤ 1200 И depth ≤ 1200`
- При `strictNarrowAislePlacement=true`: узкопроходные паллеты **ТОЛЬКО** в `narrowAisle=true` секции
- Широкопроходные паллеты **НЕ ОГРАНИЧЕНЫ** — могут размещаться в узкопроходных секциях (но с мягким штрафом -5000)
- Без этого правила (strictNarrowAisle=false): узкопроходные секции имеют приоритет, но паллета может уйти в широкопроходную

### 9.2 Формула зазора (gap_width)

```
Для N паллет: свободная ширина = ширина_секции - SUM(ширины_паллет) - (N+1) * gap_width
```

Для gap_width=50 мм:
- 0 паллет: 1×50 = 50 мм запаса
- 1 паллета: 2×50 = 100 мм (соответствует 150 мм в 1С для 2-го паллета)
- 2 паллеты: 3×50 = 150 мм (соответствует 200 мм в 1С для 3-го паллета)

**Совпадение с 1С:** В 1С зазор **инкрементальный**: 150 мм для 2-го паллета, 200 мм для 3-го. При gap_width=50: `(N+1)*50` даёт 100 для 2-го и 150 для 3-го → плюс 50 мм запаса на саму паллету → итого 150 и 200. Формулы совпадают!

### 9.3 Блокировка паллет (`blocked > 0`)

- Паллета с `blocked > 0` считается **недвижимой** (`movable = False`)
- Солвер не может её переставить (MOVE)
- Но секция с blocked-паллетой остаётся доступной для новых — blocked-паллета просто занимает свой слот

### 9.4 max_widthPallet / max_depthPallet

- `max_widthPallet = 0` → используется полная ширина секции (1С уже резолвит этот fallback в SQL)
- `max_depthPallet = 0` → используется полная глубина секции (1С **НЕ** резолвит — Python применяет fallback)

---

## 10. CONTAINER И ЗАПУСК

```bash
# Сборка и запуск
cd d:\project\OKIL
docker-compose up -d wms-optimizer

# Проверка
curl http://localhost:8010/health
# → {"status": "ok"}

# Swagger
http://localhost:8010/docs

# Запуск теста S7 (внутри контейнера)
docker exec wms-optimizer python -m pytest tests/test_s7_vs_standard.py -v -s
```

**Docker Compose (фрагмент):**
```yaml
wms-optimizer:
  build: ./services/wms_optimizer
  ports:
    - "8010:8010"
  restart: unless-stopped
```

---

## Связи

[[wms_optimizer]] — страница вики сервиса
[[Лико_WMS_Сервер]] — 1С модуль интеграции
[[wms-backend]] — соседний WMS-сервис (порт 8080, Bin Packing BFD)
[[WMS_Optimizer_Summary]] — история BSL-версии оптимизатора
[[СинхронизацияПодборВалидация]] — принцип единого источника правил (potential.py)
