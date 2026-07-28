# ТЗ: WMS Pallet Optimizer — Self-Contained Handoff

> **Дата:** 2026-07-28 | **Версия:** v1.0
> **Репозиторий:** OKIL | **Сервис:** `services/wms_optimizer`
> **Порт:** 8010 | **Docker:** `wms-optimizer`

Полное техническое задание для LLM-модели на задачу оптимального размещения паллет на складе.
Самодостаточный документ — не требует wiki-ссылок или доступа к репозиторию.

---

## 1. System Overview

**WMS Pallet Optimizer** — Python-сервис на FastAPI + Google OR-Tools CP-SAT, решающий задачу оптимального размещения паллет на складе. Принимает **снимок склада** (секции + адреса + текущие паллеты) и **список новых паллет** к размещению. Возвращает **оптимальный план** — какие паллеты в какие адреса поставить, какие существующие передвинуть.

### 1.1 Два уровня оптимизации

| Уровень | Модуль | Задача | Метод |
|---------|--------|--------|-------|
| **Глобальный** | `solver/cp_sat_aggregated.py` | Паллета → Секция | CP-SAT (OR-Tools), агрегированная модель по типоразмерам |
| **Локальный** | `optimizer/section_optimizer.py` | Паллета → Адрес внутри секции | Детерминированные правила (как в 1С) |

### 1.2 Ключевые цифры (S7, холодный старт, 3406 паллет)

| Метрика | Одноэтапный | Двухэтапный | Ручной эталон S6 |
|---------|:-----------:|:-----------:|:----------------:|
| Размещено | 3240 (95.1%) | **3332 (97.8%)** | 3242 (95.2%) |
| Время | 188 сек | 252 сек | вручную |
| CP-SAT переменных | ~1000 | ~1000 + ~500 | N/A |

**Вывод:** Двухэтапный оптимизатор превосходит человека на **90 паллет (+2.8%)**.

---

## 2. Architecture

### 2.1 Дерево модулей

```
wms_optimizer/
├── main.py                          # FastAPI app, точка входа uvicorn
├── api/
│   ├── routes.py                    # Эндпоинты: /api/optimize, /api/optimize/async
│   └── schemas.py                   # ВЕСЬ Pydantic-контракт (request/response/enums)
├── models/
│   ├── pallet.py                    # Pallet, PalletTypeSize
│   ├── section.py                   # Section, SectionTypeSize
│   ├── address.py                   # Address (position 1/2/3)
│   └── occupancy_builder.py         # Парсинг occupancy JSON → Section/Address/Pallet
├── optimizer/
│   ├── global_optimizer.py          # Оркестратор: run_optimization()
│   ├── two_stage_optimizer.py       # Двухэтапный режим
│   ├── section_optimizer.py         # Локальный: Паллета → Адрес (5 правил)
│   ├── potential.py                 # section_fits_pallet() — единый источник правил
│   └── scoring.py                   # compute_global_score / compute_address_score
├── solver/
│   ├── cp_sat_aggregated.py         # Агрегированная CP-SAT модель (Фаза C)
│   ├── cp_sat_model.py              # Точная CP-SAT модель (для малых задач)
│   ├── aggregation.py               # Группировка типоразмеров паллет/секций
│   ├── feasibility.py               # compute_feasible_pairs — допустимые пары
│   ├── warm_start.py                # First-Fit-Decreasing эвристика
│   ├── numpy_solver.py              # NumPy greedy solver (solverType="numpy")
│   ├── lp_solver.py                 # LP simplex solver (solverType="lp")
│   └── config.py                    # Пороги, num_search_workers
├── validation/
│   └── validator.py                 # Валидация входного запроса
└── config/
    ├── weights.json                 # Веса целевой функции
    └── settings.json                # defaultSettings, порт 8010
```

### 2.2 Data Flow

```
1C WMS_GetOccupancy() → JSON (occupancy + newPallets)
    ↓
POST /api/optimize
    ↓
build_warehouse_state() → Section[], Address[], Pallet[]
    ↓
[ twoStageReslot? → two_stage_optimizer ]
    ↓
first_fit_decreasing() → Warm Start
    ↓
compute_feasible_pairs() → выбор модели (aggregated vs exact)
    ↓
CP-SAT solver.solve() → {pallet_id: section_id}
    ↓
assign_addresses() → {pallet_id: address_id}
    ↓
Формирование delta-операций (PUT для новых, MOVE для перемещённых)
    ↓
OptimizationResponse → 1С WMS_PlacePallets()
```

---

## 3. Input Contract

### 3.1 Endpoint

```
POST /api/optimize
Content-Type: application/json
```

### 3.2 OptimizationRequest

```json
{
  "optimizationId": "uuid-строка",
  "mode": "place",              // "place" | "compact"
  "occupancy": [ /* OccupancySection[] */ ],
  "newPallets": [ /* NewPallet[] */ ],
  "settings": { /* OptimizationSettings */ }
}
```

- `mode="place"` — размещение новых паллет с опциональным реслотом существующих
- `mode="compact"` — только реслот существующих (newPallets должен быть пустым)

### 3.3 OccupancySection — текущее состояние секции

**Источник:** 1С → `Лико_WMS_Сервер.СобратьЗанятостьСекций()`. Одна строка = одна секция склада.

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
| `typeSize_width` | float | — | Ширина секции, мм **(> 0)** |
| `typeSize_height` | float | — | Высота секции, мм **(> 0)** |
| `typeSize_depth` | float | — | Глубина секции, мм **(> 0)** |
| `typeSize_weight` | float | — | Грузоподъёмность секции, кг |
| `typeSize_unlimitedWeight` | bool | false | Вес секции не ограничен |
| `gap_width` | float | — | Зазор между паллетами, мм **(≥ 0)** |
| `max_lift_weight` | float | — | Макс. вес подъёма одной паллеты, кг |
| `max_pallets` | int | 3 | Макс. количество паллет в секции |
| `max_widthPallet` | float | 0 | Макс. ширина ОДНОЙ паллеты. 0 = нет ограничения |
| `max_depthPallet` | float | 0 | Макс. глубина ОДНОЙ паллеты. 0 = нет ограничения |

#### Правила доступа

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `narrowAisle` | bool | false | Узкопроходная секция |
| `restricted` | bool | false | Секция заблокирована — **полностью исключается** |
| `accessLevel` | int | 1 | Резерв |
| `accessTime` | float | 0 | Резерв |

#### Текущие паллеты в секции (слоты 1, 2, 3)

Для каждого слота N ∈ {1, 2, 3}:

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `addressN` | UUID строка | `""` | GUID адреса. `""` = слот не существует |
| `palletN_id` | UUID строка | `""` | GUID паллеты. `""` = пусто |
| `palletN_code` | строка | `""` | Код паллеты |
| `palletN_width` | float | 0 | Ширина паллеты |
| `palletN_height` | float | 0 | Высота паллеты |
| `palletN_depth` | float | 0 | Глубина паллеты |
| `palletN_weight` | float | 0 | Вес паллеты |
| `quantityN` | float | 0 | Количество товара. 0 + не blocked = слот свободен |
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
  "accessLevel": 1 // резерв
}
```

### 3.5 OptimizationSettings

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| `allowReslot` | bool | true | Разрешить переставлять существующие паллеты |
| `maxReslotPercent` | float | 20 | Макс. % секций для реслота (0-100) |
| `maxOperations` | int | 300 | Лимит операций PUT+MOVE |
| `timeLimitSeconds` | int | 120 | Таймаут CP-SAT солвера |
| `strictNarrowAislePlacement` | bool | **true** | Узкопроходные паллеты ТОЛЬКО в узкопроходные секции |
| `twoStageReslot` | bool | **false** | Двухэтапный режим |
| `twoStageReslotMaxReslotPercent` | float | **10.0** | maxReslotPercent для ЭТАПА 2 |
| `twoStageReslotTimeLimitSeconds` | int | **120** | timeLimitSeconds для ЭТАПА 2 |
| `solverType` | string | `"cp_sat"` | Тип солвера: `"cp_sat"` / `"numpy"` / `"lp"` |

### 3.6 Пример запроса

```json
{
  "optimizationId": "test-001",
  "mode": "place",
  "occupancy": [
    {
      "section_id": "uuid-sec-1",
      "section_code": "Р601-М(01-02-03)-Э01",
      "rack_id": "uuid-rack-1",
      "rack_code": 1,
      "floor": 1,
      "typeSize_width": 2700,
      "typeSize_height": 1750,
      "typeSize_depth": 1200,
      "typeSize_weight": 10000,
      "typeSize_unlimitedWeight": false,
      "gap_width": 50,
      "max_lift_weight": 10000,
      "max_pallets": 3,
      "max_widthPallet": 1200,
      "max_depthPallet": 1200,
      "narrowAisle": false,
      "restricted": false,
      "accessLevel": 1,
      "accessTime": 10,
      "address1": "Р601М1Э1", "address2": "Р601М2Э1", "address3": "Р601М3Э1",
      "pallet1_id": "00000000-0000-0000-0000-000000000000",
      "pallet1_width": 0, "pallet1_height": 0, "pallet1_depth": 0, "pallet1_weight": 0,
      "pallet1_code": "", "quantity1": 0, "blocked1": 0,
      "pallet2_id": "00000000-0000-0000-0000-000000000000",
      "pallet2_width": 0, "pallet2_height": 0, "pallet2_depth": 0, "pallet2_weight": 0,
      "pallet2_code": "", "quantity2": 0, "blocked2": 0,
      "pallet3_id": "00000000-0000-0000-0000-000000000000",
      "pallet3_width": 0, "pallet3_height": 0, "pallet3_depth": 0, "pallet3_weight": 0,
      "pallet3_code": "", "quantity3": 0, "blocked3": 0
    }
  ],
  "newPallets": [
    {"id": "FLOOR-0001", "width": 800, "height": 1200, "depth": 1200, "weight": 350, "accessLevel": 1}
  ],
  "settings": {
    "allowReslot": false,
    "maxOperations": 5000,
    "timeLimitSeconds": 180,
    "twoStageReslot": true,
    "twoStageReslotMaxReslotPercent": 10.0,
    "twoStageReslotTimeLimitSeconds": 120,
    "solverType": "numpy"
  }
}
```

---

## 4. Output Contract

### 4.1 OptimizationResponse

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

### 4.2 solverStatus

| Статус | Значение |
|--------|---------|
| `OPTIMAL` | Найден гарантированно оптимальный план |
| `FEASIBLE` | Найдено допустимое решение (не гарантированно оптимальное) |
| `TIME_LIMIT` | Таймаут — лучшее найденное на момент остановки |
| `INFEASIBLE` | Решения не существует |

### 4.3 placementStatus

| Статус | Значение |
|--------|---------|
| `FULL` | Все паллеты размещены |
| `PARTIAL` | Часть размещена, часть в notPlaced |
| `NONE` | Ни одна не размещена |

### 4.4 Operation

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

### 4.5 NotPlaced — неразмещённая паллета

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

| Код | Условие |
|-----|---------|
| `HEIGHT_LIMIT` | Высота паллеты > высоты всех секций |
| `NARROW_AISLE_MISMATCH` | Узкая паллета + нет узкопроходных секций + strictNarrowAisle=true |
| `NO_SPACE` | Нет свободного места по размерам/количеству |
| `RESLOT_LIMIT` | Место есть, но занято/запрещён реслот |
| `DEPTH_LIMIT` | Глубина паллеты > глубины всех секций |
| `LIFT_LIMIT` | Вес паллеты > max_lift_weight всех секций |
| `MAX_PALLET_SIZE_LIMIT` | Ширина/глубина > max_widthPallet/max_depthPallet |
| `WEIGHT_LIMIT` | Суммарный вес превышает грузоподъёмность |

### 4.6 Metrics

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

## 5. Complete Placement Rules (17 проверок 1С)

Все правила из функции `ОшибкиРазмещенияВАдрес()` (`ManagerModule.bsl:2696`). Каждая проверка задокументирована с точным условием 1С и Python-эквивалентом.

### Проверка 0: Валидация адреса
- **1С (строка 2765):** `Если Не ЗначениеЗаполнено(Ячейка) Тогда`
- **Python:** Проверка `target_addr is not None` в `address_by_id`

### Проверка 1: Адрес уже занят
- **1С (строка 2817):** `Если ЗначениеЗаполнено(СооАдресовСекции[Ячейка]) Тогда`
- **Python:** Проверка `virtual_state.get(target_addr_id) is not None`
- **Ошибка:** `ADDRESS_OCCUPIED`

### Проверка 2: Запрет размещения (restricted)
- **1С (строка 2831):** `Если Секция.ЗапретРазмещения Тогда`
- **Python:** Секции с `restricted=True` исключаются на этапе `build_warehouse_state()` и не попадают в оптимизацию

### Проверки 3-10: Геометрические конфликты адресов

**Принцип секции (3 адреса — 1 левый, 2 центр, 3 правый):**

| Правило | Условие | Разрешённые адреса | Макс. паллет |
|---------|---------|-------------------|:-----------:|
| Паллета > 2W/3 | Ширина > 2/3 ширины секции | Только центр (адрес 2) | 1 |
| Паллета > W/3 | Ширина > 1/3 ширины секции | Только края (1, 3) | 2 |
| Паллета ≤ W/3 | Ширина ≤ 1/3 ширины секции | Все (1, 2, 3) | 3 |

**Конфликты:**

| # | 1С строка | Условие | Python-ошибка |
|---|-----------|---------|---------------|
| 3 | 2840 | Адрес 1 + центр занят паллетой > W/3 → адрес 1 заблокирован | `ADDR1_BLOCKED_BY_WIDE_CENTER` |
| 4 | 2852 | Адрес 3 + центр занят паллетой > W/3 → адрес 3 заблокирован | `ADDR3_BLOCKED_BY_WIDE_CENTER` |
| 5 | 2864 | Центр + адрес 1 занят паллетой > W/3 → центр заблокирован | `ADDR2_BLOCKED_BY_WIDE_ADDR1` |
| 6 | 2876 | Центр + адрес 3 занят паллетой > W/3 → центр заблокирован | `ADDR2_BLOCKED_BY_WIDE_ADDR3` |
| 7 | 2888 | Центр + адрес 1 занят, НОВЫЙ паллет > W/3 → не влезет | `WIDE_PALLET_CENTER_WITH_ADDR1` |
| 8 | 2900 | Центр + адрес 3 занят, НОВЫЙ паллет > W/3 → не влезет | `WIDE_PALLET_CENTER_WITH_ADDR3` |
| 9 | 2912 | Адрес 1 + НОВЫЙ паллет > 2W/3 → только центр | `WIDE_PALLET_ON_EDGE_ADDR1` |
| 10 | 2924 | Адрес 3 + НОВЫЙ паллет > 2W/3 → только центр | `WIDE_PALLET_ON_EDGE_ADDR3` |

### Проверка 11: Ширина паллета > ширины секции
- **1С (строка 2936):** `Секция.Типоразмер.Ширина < Структура.Ширина`
- **Python:** `section_fits_pallet()` → `total_width + total_gap > section.width`
- **Ошибка:** `WIDTH_OVERFLOW`

### Проверка 12: Высота паллета > высоты секции
- **1С (строка 2949):** `Секция.Типоразмер.Высота < Структура.Высота`
- **Python:** `pallet.height > section.height`
- **Ошибка:** `HEIGHT_LIMIT`

### Проверка 13: Глубина паллета > глубины секции
- **1С (строка 2962):** `Секция.Типоразмер.Глубина < Структура.Глубина`
- **Python:** `pallet.depth > section.depth`
- **Ошибка:** `DEPTH_LIMIT`

### Проверка 14: Общий вес > грузоподъёмности секции
- **1С (строка 2974-2990):** Сумма весов занятых + нового > `Типоразмер.Вес` (кроме `НеОграниченаПоГрузоподъемности`)
- **Python:** `total_weight > section.max_weight` (где `max_weight = inf` при `unlimited_weight`)
- **Ошибка:** `WEIGHT_OVERFLOW`

### Проверка 15: Монтажный зазор при дозаполнении
- **1С (строка 2992-3013):**
  ```
  ОстатокШиринаСекции = Типоразмер.Ширина - ШиринаЗанятая
  Если КоличествоПаллетВСекции >= 1 Тогда
      ТребуемыйЗазор = ?(КоличествоПаллетВСекции = 1, 150, 200)
      Если ОстатокШиринаСекции - Структура.Ширина - ТребуемыйЗазор < 0 Тогда ...
  ```
- **Python:**
  ```python
  if existing_count >= 1:
      extra_gap = 150 if existing_count == 1 else 200
      if section.width - occupied_width - p_w - extra_gap < 0: ...
  ```
- **Ошибка:** `MOUNTING_GAP`
- **Формула gap_width vs монтажный зазор:**
  В Python используется формула `(N+1)*gap_width` (где N — текущее количество паллет, gap_width=50 мм).
  При N=1: `(1+1)*50 = 100` → плюс 50 мм запаса на саму паллету → **150 мм**
  При N=2: `(2+1)*50 = 150` → плюс 50 мм запаса → **200 мм**
  Формулы эквивалентны.

### Проверка 16: Вес паллета > максимальный вес подъёма на этаж
- **1С (строка 3016):** `Секция.МаксимальныйВесПодъёмаНаЭтаж > 0 И ... < Структура.Вес`
- **Python:** `p_wt > section.max_lift_weight` (где `max_lift_weight` не `inf`)
- **Ошибка:** `LIFT_WEIGHT_LIMIT`

### Проверки 17a-d: Габариты по стеллажу
- **1С (строки 3027-3042):**
  - 17a: `Стеллаж.МинШиринаПаллета <> 0 И Структура.Ширина < Стеллаж.МинШиринаПаллета`
  - 17b: `Стеллаж.МаксШиринаПаллета <> 0 И Структура.Ширина > Стеллаж.МаксШиринаПаллета`
  - 17c: Мин глубина (пропускается если `МинимальнаяГлубинаНеОграничена`)
  - 17d: Макс глубина (пропускается если `МаксимальнаяГлубинаНеОграничена`)
- **Python:** Не покрыты в текущей версии (rack-level ограничения не передаются в `OccupancySection`)

### Проверка (доп.): eff_max_width / eff_max_depth
- **Python:**
  - `max_widthPallet`: берётся из occupancy, 0 → fallback на ширину секции
  - `max_depthPallet`: берётся из occupancy, 0 → fallback на глубину секции
  - `eff_max_width = max_widthPallet if max_widthPallet > 0 else section.width`
  - `eff_max_depth = max_depthPallet if max_depthPallet > 0 else section.depth`
- **Ошибка:** `EFF_MAX_WIDTH`

---

## 6. Address Selection Rules

### 6.1 Python: `section_optimizer.assign_addresses()` — 5 правил

После того как глобальный оптимизатор назначил `паллета → секция`, section optimizer выбирает конкретный адрес (position 1/2/3):

1. **Паллета > 2W/3** → Адрес2 (центр), если свободен. Иначе не размещена.
2. **Адрес1 свободен** → Адрес1
3. **Адрес3 свободен** → Адрес3
4. **Адрес2 свободен** → Адрес2
5. **Всё занято** → не размещена (None)

### 6.2 1С: `ПодобратьЯчейку()` — 13 приоритетов ранжирования секций

1С не просто проверяет "влезает/не влезает", а ранжирует ВСЕ подходящие секции по 13 критериям:

1. Узкопроходные стеллажи первыми (УБЫВ)
2. Ограничение глубины первыми (с бортиками раньше, с дном позже)
3. Итоговый потенциал секции (УБЫВ для узких паллет, ASC для широких)
4. Best-fit по высоте (ASC)
5. Best-fit по грузоподъёмности (ASC)
6. Потенциал по ширине (УБЫВ)
7. Потенциал по весу (УБЫВ)
8. Best-fit по весу ячейки (ASC)
9. Полная грузоподъёмность секции (ASC)
10. Остаток ширины секции (ASC)
11. Остаток веса секции (ASC)
12. Горизонтальная зона доступа (ASC — ближние первыми)
13. Время доступа (ASC — быстрые первыми)

**Python-оптимизатор НЕ реализует это ранжирование** — вместо этого используется CP-SAT с целевой функцией (максимизация placed + бонус за narrow→narrow).

---

## 7. Algorithms

### 7.1 CP-SAT Агрегированная модель (`cp_sat_aggregated.py`)

**Ключевая идея:** Паллеты одного типоразмера взаимозаменяемы. Вместо `X[паллета, секция]` (булева, ~2.35M переменных для S7) → `Y[тип_паллеты, бакет_секций]` (целочисленная, ~1000 переменных).

**Бакетизация секций** (`aggregation.py`):
- Секции группируются по одинаковому остатку вместимости
- Ключ бакета = `(height, depth, max_lift_weight, eff_max_width, eff_max_depth, narrow_aisle, gap_width, remaining_count, remaining_width, remaining_weight)`
- `_BUCKET_CHUNK_SIZE = 1` — каждый бакет = 1 секция (гарантирует совпадение с физической упаковкой)

**Переменные и ограничения:**
```python
Y[(type_key, bucket_idx)] = model.NewIntVar(0, min(n_type, bucket_total_count))

# sum(Y[type, :]) ≤ count(type)          — не больше, чем есть паллет
# sum(Y[:, bucket]) ≤ bucket.total_count  — не больше слотов в бакете
# sum(width * Y) + count * gap ≤ budget   — ширина с зазорами
# sum(weight * Y) ≤ budget               — вес
# sum(all Y) ≤ maxOperations              — лимит операций
```

**Целевая функция:**
```python
Maximize:
  100000 * total_placed           // максимизировать размещённые
  + 10 * narrow_in_narrow         // бонус: узкая → узкая секция
  - 5000 * wide_in_narrow         // штраф: широкая → узкая секция
```

**Дезагрегация (5 шагов):**
1. Best-fit внутри бакета: самые широкие паллеты первыми, предпочитаем секции с большей занятостью
2. Mismatch → fallback по всему складу
3. `_resolve_residual_exact` — точная CP-SAT на хвосте
4. `_resolve_residual_with_consolidation` — уплотнение
5. `_resolve_residual_with_reslot` — виртуальный реслот

### 7.2 First-Fit-Decreasing Warm Start (`warm_start.py`)

FFD эвристика готовит `AddHint` для CP-SAT:
- Сортируем паллеты по убыванию площади (width * depth)
- Для каждой паллеты: первая секция куда влезает → назначаем
- Проверка через `section_fits_pallet()` (единый источник правил)

### 7.3 Section Optimizer (`section_optimizer.py`)

См. раздел 6.1 — 5 детерминированных правил.

### 7.4 Двухэтапный режим (`two_stage_optimizer.py`)

```
ЭТАП 1 (без реслота, allowReslot=False):
  run_optimization(stage1) → размещено N1 паллет
  Если всё размещено → return

ЭТАП 2 (реслот остатков, allowReslot=True, maxReslotPercent=10%):
  _build_occupancy_after_stage1() → occupancy с паллетами ЭТАПА 1
  not_placed = только неразмещённые паллеты
  run_optimization(stage2) → размещено N2 паллет

Объединение:
  total_placed = N1 + N2
  Дедупликация операций: последняя операция для каждой паллеты
```

### 7.5 Feasibility: `section_fits_pallet()` (`potential.py:96-133`)

**ЕДИНСТВЕННАЯ функция** проверки "влезает ли паллета в секцию". Используется на всех уровнях: CP-SAT, дезагрегация, warm start, валидация.

```python
def section_fits_pallet(section, pallets_in_section, pallet, strict_narrow=True):
    # 1. strict_narrow: узкая паллета → только narrow_aisle секции
    # 2. Количество: len(pallets_in_section) < max_pallets
    # 3. Высота паллеты ≤ высота секции
    # 4. Глубина паллеты ≤ глубина секции
    # 5. Вес паллеты ≤ max_lift_weight
    # 6. Ширина паллеты ≤ eff_max_width
    # 7. Глубина паллеты ≤ eff_max_depth
    # 8. Ширина: sum(widths) + pallet.width + (N+2)*gap_width ≤ section.width
    # 9. Вес: sum(weights) + pallet.weight ≤ max_weight
```

---

## 8. Geometric Rules

### 8.1 Правила ширины паллеты относительно секции

| Ширина паллеты | Разрешённые адреса | Макс. паллет в секции |
|:--------------|:-------------------|:---------------------:|
| > 2W/3 | Только центр (2) | 1 |
| > W/3 | Только края (1, 3) | 2 |
| ≤ W/3 | Все (1, 2, 3) | 3 |

### 8.2 Блокировки адресов

```
Широкая паллета (>W/3) в центре (2) → ОБА края (1, 3) заблокированы
Широкая паллета (>W/3) на краю (1) → Центр (2) заблокирован
Широкая паллета (>W/3) на краю (3) → Центр (2) заблокирован
```

### 8.3 Монтажный зазор

| Существующих паллет | Дополнительный зазор | Формула gap_width (50 мм) |
|:-------------------:|:--------------------:|:-------------------------:|
| 1 | 150 мм | (1+1)*50 + 50 = 150 |
| 2 | 200 мм | (2+1)*50 + 50 = 200 |

**1С-формула:** `ОстатокШиринаСекции - ШиринаПаллета - ТребуемыйЗазор >= 0`
**Python-формула:** `sum(widths) + new_width + (N+2)*gap_width <= section.width`

Обе формулы дают одинаковый результат при `gap_width = 50 мм`.

---

## 9. Key Results

### 9.1 Конфигурация теста S7

```python
SETTINGS = OptimizationSettingsSchema(
    allowReslot=False,
    maxOperations=5000,
    timeLimitSeconds=180,
    twoStageReslot=True,
    twoStageReslotMaxReslotPercent=10.0,
    twoStageReslotTimeLimitSeconds=120,
)
```

- `warehouse7.json` — 1530 секций, все пустые (холодный склад)
- `floor7.json` — 3406 паллет с пола
- `warehouse6_standard.json` — ручная раскладка человека: **3242/3406 (95.2%)**

### 9.2 Результаты

| Подход | Размещено | Время | Статус |
|--------|:---------:|:-----:|:------:|
| Жадный (FFD) | 2440 (71.6%) | ~5s | ❌ |
| CP-SAT точная модель | 3234 (94.9%) | ~20 мин | ⚠️ |
| CP-SAT агрегированная (одноэтапная) | 3240 (95.1%) | 188s | ✅ |
| **CP-SAT агрегированная (двухэтапная)** | **3332 (97.8%)** | **252s** | **✅** |
| Ручное (эталон S6) | 3242 (95.2%) | вручную | Baseline |

### 9.3 Анализ неразмещённых (74 паллеты из 3406)

- **HEIGHT_LIMIT (~100 на этапе 1):** Паллеты высотой > 2000 мм — выше всех секций. Жёсткое ограничение.
- **NARROW_AISLE_MISMATCH (~64):** Узкопроходные паллеты (≤1200×1200), все узкопроходные секции заполнены. При `strictNarrowAislePlacement=true` — жёсткое ограничение.

---

## 10. Narrow Aisle Rules

### 10.1 Определение

Паллета считается **узкопроходной (narrow)** если:
```python
pallet.width <= 1200 AND pallet.depth <= 1200
```

### 10.2 Поведение

- `strictNarrowAislePlacement=true` (по умолчанию):
  - Узкопроходные паллеты **ТОЛЬКО** в `narrowAisle=true` секции
  - Широкопроходные паллеты могут размещаться в узкопроходных секциях (штраф -5000)

- `strictNarrowAislePlacement=false`:
  - Узкопроходные секции имеют приоритет, но паллета может уйти в широкопроходную

### 10.3 Целевая функция

```python
+ 10 * narrow_in_narrow     # бонус за размещение узкой паллеты в узкой секции
- 5000 * wide_in_narrow     # штраф за размещение широкой паллеты в узкой секции
```

---

## 11. 1C Integration

### 11.1 Отправка запроса из 1С

```bsl
// 1. Получить снимок склада
Параметры = Новый Структура("warehouse", Строка(Склад.УникальныйИдентификатор()));
Ответ = Лико_WMS_Сервер.WMS_GetOccupancy(Параметры);
Occupancy = ПрочитатьJSON(Новый ЧтениеJSON(Ответ.Тело));

// 2. Отправить в оптимизатор
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
Запрос.Вставить("settings", Settings);

HTTPСоединение = Новый HTTPСоединение("localhost", 8010);
HTTPЗапрос = Новый HTTPЗапрос("/api/optimize");
HTTPЗапрос.Заголовки.Вставить("Content-Type", "application/json; charset=utf-8");
HTTPЗапрос.УстановитьТелоИзСтроки(Лико_HTTP_Сервер.СтруктураВJSON(Запрос));

HTTPОтвет = HTTPСоединение.ВызватьHTTPМетод("POST", HTTPЗапрос);
Результат = ПрочитатьJSON(Новый ЧтениеJSON(HTTPОтвет.ПолучитьТелоКакСтроку()));
```

### 11.2 Выполнение плана в 1С

```bsl
// PUT — размещение новых паллет
МассивPlacements = Новый Массив;
Для Каждого Op Из Результат.operations Цикл
    Если Op.operation = "PUT" Тогда
        Placement = Новый Структура;
        Placement.Вставить("pallet", Op.pallet);
        Placement.Вставить("address", Op.newAddress);
        МассивPlacements.Добавить(Placement);
    КонецЕсли;
КонецЦикла;

// MOVE — перемещение существующих
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
```

### 11.3 Валидация в 1С

Для каждого размещения `WMS_PlacePallets` вызывает:
```bsl
Ошибки = Справочники.Лико_СкладскиеСекции.ОшибкиРазмещенияПаллетаВАдрес(Паллет, Адрес);
```
Все 17 проверок из раздела 5 выполняются на стороне 1С.

---

## 12. Data File Reference

### 12.1 `warehouse7.json` — Склад S7

- **Секций:** 1530 (все пустые — холодный старт)
- **Размер:** ~4 MB (79 564 строки)
- **Формат:** `{"sections": [{...}, ...]}` — каждая секция как `OccupancySectionSchema`
- **Параметры секций:** ширина 2700 мм, высота 1750 мм, глубина 1200 мм, gap 50 мм, max_pallets=3
- **Стеллажи:** rack_code 1-9, 4-5 этажей в каждом
- **Узкопроходные:** часть секций имеют `narrowAisle=true`, `max_widthPallet=1200`, `max_depthPallet=1200`

### 12.2 `floor7.json` — Паллеты с пола S7

- **Паллет:** 3406
- **Размер:** ~1.5 MB (47 688 строк)
- **Формат:** `{"floorPallets": [{width, height, depth, weight, ...}, ...]}`
- **Типичные габариты:** 800×1200×1200 (350 кг), 1200×1705×1200 (1500 кг)
- **Высокие паллеты:** часть паллет имеют высоту > 1750 мм — неразмещаемы (HEIGHT_LIMIT)

### 12.3 `warehouse6_standard.json` — Ручной эталон S6

- **Секций:** 1530 (заполнены вручную)
- **Размещено:** 3242 паллеты из 3406 (95.2%)
- **Размер:** ~4 MB (79 564 строки)
- **Формат:** Тот же `{"sections": [{...}, ...]}` — но секции заполнены паллетами
- **Использование:** подсчёт количества размещённых паллет через `build_warehouse_state()` → baseline для регрессионного теста

---

## A. Тестовый скрипт

Запуск:
```bash
cd services/wms_optimizer
python docs/handoff/test_handoff_validation.py
# или
pytest docs/handoff/test_handoff_validation.py -v -s
```

Скрипт:
1. Загружает `warehouse7.json` и `floor7.json` из handoff-директории
2. Запускает оптимизатор (two-stage, numpy solver)
3. Симулирует ВСЕ 17 проверок 1С на каждой операции
4. Сравнивает с ручным эталоном из `warehouse6_standard.json`
5. Выводит детальный отчёт по ошибкам

**Критерий успеха:** 0 ошибок валидации, `placedPallets >= 3242` (не хуже человека).
