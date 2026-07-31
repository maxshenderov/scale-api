# WMS Pallet Optimizer — API Documentation v1.0

> Python-сервис оптимального размещения паллет на складе  
> OR-Tools CP-SAT solver + FFD warm start + Section Optimizer  
> Docker: `wms-optimizer:8010` | Swagger: http://localhost:8010/docs

---

## Быстрый старт

```bash
# Запуск Docker
docker-compose up -d wms-optimizer
curl http://localhost:8010/health

# Swagger UI
http://localhost:8010/docs
```

---

## API Endpoints

### 1. GET /health

Проверка работоспособности сервиса.

**Response 200:**
```json
{"status": "ok"}
```

---

### 2. POST /api/optimize (синхронный режим)

Блокирует соединение до завершения расчёта.

**Когда использовать:**
- < 500 паллет
- timeLimitSeconds ≤ 120
- Нужен результат немедленно

**Request:**
```json
{
  "optimizationId": "uuid-string",
  "mode": "place",
  "occupancy": [ /* массив секций из WMS_GetOccupancy */ ],
  "newPallets": [ /* массив паллет с пола */ ],
  "settings": {
    "allowReslot": false,
    "maxReslotPercent": 0,
    "maxOperations": 300,
    "timeLimitSeconds": 120,
    "solverType": "cp_sat"
  }
}
```

**Response 200:**
```json
{
  "optimizationId": "uuid",
  "mode": "place",
  "solverStatus": "OPTIMAL",
  "placementStatus": "FULL",
  "score": 95000.0,
  "executionTimeSeconds": 12.34,
  "operations": [
    {
      "pallet": "pallet-uuid",
      "operation": "PUT",
      "oldAddress": null,
      "newAddress": "address-uuid",
      "sequence": 1
    }
  ],
  "notPlaced": [],
  "metrics": {
    "placedPallets": 50,
    "notPlacedPallets": 0,
    "movedPallets": 0,
    "potentialLoss": 0,
    "usedSections": 48
  }
}
```

**Response 422:** Ошибка валидации
**Response 500:** Внутренняя ошибка солвера

---

### 3. POST /api/optimize/async (асинхронный режим)

Возвращает optimizationId немедленно, расчёт идёт в фоне.

**Когда использовать:**
- > 500 паллет
- timeLimitSeconds > 120
- Не нужно блокировать клиента

**Request:** тот же что и `/api/optimize`, НО `optimizationId` обязательно!

**Response 202:**
```json
{
  "optimizationId": "uuid",
  "status": "PENDING",
  "progress": 0
}
```

---

### 4. GET /api/optimization/{id}

Статус асинхронной задачи.

**Response 200:**
```json
{
  "optimizationId": "uuid",
  "status": "RUNNING",
  "progress": 10
}
```

**status:** `PENDING` | `RUNNING` | `COMPLETED` | `FAILED`

---

### 5. GET /api/optimization/{id}/result

Результат завершённой задачи.

**Response 200:** OptimizationResponse (если COMPLETED)  
**Response 202:** Расчёт ещё идёт  
**Response 500:** Ошибка (если FAILED)  
**Response 404:** Задача не найдена

---

## Схемы данных

### OptimizationRequest

| Поле | Тип | Обязательное | Описание |
|------|-----|:---:|---------|
| optimizationId | string | ✗ | UUID запроса. Для async — обязательно |
| mode | `place` / `compact` | ✗ | place=разместить новые, compact=уплотнить без новых |
| occupancy | array | ✓ | Секции склада из WMS_GetOccupancy |
| newPallets | array | ✓* | Паллеты для размещения (*только при mode=place) |
| settings | object | ✗ | Настройки солвера |

### OccupancySection — занятость секций (текущее состояние склада)

**Occupancy** = снимок текущего состояния склада на момент запроса оптимизации. Одна строка = одна секция со всеми её параметрами (размеры, ограничения, правила) и до 3 паллет, которые уже стоят внутри. Источник — `Лико_WMS_Сервер.СобратьЗанятостьСекций()`. Поля 1:1 соответствуют `OccupancySectionSchema` ([api/schemas.py](api/schemas.py)).

**Идентификация секции:**

| Поле | Тип | Обязательное | Описание |
|------|-----|:---:|---------|
| section_id | UUID (string) | ✓ | GUID секции |
| section_code | string | ✓ | Код секции, например `"Р601-М(01-02-03)-Э01"` |
| rack_id | UUID (string) | ✓ | GUID стеллажа |
| rack_code | int | ✓ | Номер стеллажа |
| floor | int | ✓ | Этаж/ярус. **Не используется в алгоритме** |

**Правила и ограничения секции:**

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| accessLevel | int | 1 | Резерв на будущее. **Не используется в алгоритме** |
| accessTime | float | 0 | Резерв на будущее. **Не используется в алгоритме** |
| restricted | bool | false | Секция заблокирована — исключается из оптимизации полностью |
| narrowAisle | bool | false | **Узкопроходная секция.** Узкопроходные секции проверяются/приоритизируются первыми для узкопроходных паллет (ширина И глубина ≤ 1200 мм). При `strictNarrowAislePlacement=true` (settings) — единственное допустимое место для таких паллет. См. [narrowAisle в rules.html](static/rules.html#narrow-aisle) |
| typeSize_width | float | — | Ширина секции, мм (обязательное, > 0) |
| typeSize_height | float | — | Высота секции, мм (обязательное, > 0) |
| typeSize_depth | float | — | Глубина секции, мм (обязательное, > 0) |
| typeSize_weight | float | — | Грузоподъёмность секции, кг (обязательное, ≥ 0). Игнорируется если `typeSize_unlimitedWeight=true` |
| typeSize_unlimitedWeight | bool | false | Вес секции не ограничен |
| gap_width | float | — | Зазор между паллетами, мм (обязательное, ≥ 0) |
| max_lift_weight | float | — | Макс. вес подъёма одной паллеты, кг (обязательное, ≥ 0) |
| max_pallets | int | 3 | Макс. количество паллет в секции |
| max_widthPallet | float | 0 | Макс. ширина ОДНОЙ паллеты, мм. `0` = нет отдельного ограничения (уже с fallback на ширину секции — резолвится в SQL 1С) |
| max_depthPallet | float | 0 | Макс. глубина ОДНОЙ паллеты, мм. `0` = нет отдельного ограничения (БЕЗ fallback, в отличие от width) |

**Текущие паллеты в секции (слоты 1-3):**

До 3 паллет на секцию, поля повторяются для N ∈ {1, 2, 3}:

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| address1 / address2 / address3 | string (UUID) | "" | GUID адреса слота N. `""` = адрес не задан |
| pallet1_id ... pallet3_id | string (UUID) | "" | GUID существующей паллеты в слоте N. `""` = слот свободен |
| pallet1_code ... pallet3_code | string | "" | Код существующей паллеты в слоте N |
| pallet1_width ... pallet3_width | float | 0 | Ширина существующей паллеты в слоте N, мм |
| pallet1_height ... pallet3_height | float | 0 | Высота существующей паллеты в слоте N, мм |
| pallet1_depth ... pallet3_depth | float | 0 | Глубина существующей паллеты в слоте N, мм |
| pallet1_weight ... pallet3_weight | float | 0 | Вес существующей паллеты в слоте N, кг |
| quantity1 ... quantity3 | float | 0 | Количество единиц товара на паллете в слоте N |
| blocked1 ... blocked3 | float | 0 | Признак блокировки: `> 0` → паллета в слоте N заблокирована, солвер её не двигает |

### NewPallet (элемент newPallets)

```json
{
  "id": "uuid",
  "width": 800,
  "height": 1500,
  "depth": 600,
  "weight": 400,
  "accessLevel": 1
}
```

**Примечание:** `accessLevel` у паллеты — резерв на будущее, сейчас не влияет на размещение.

### Settings

| Поле | Тип | Default | Описание |
|------|-----|:-------:|---------|
| allowReslot | bool | true | Разрешить переставлять стоящие паллеты |
| maxReslotPercent | float | 20 | Макс. % секций для реслота (0-100) |
| maxOperations | int | 300 | Лимит операций в плане (см. ниже) |
| timeLimitSeconds | int | 120 | Таймаут CP-SAT для основного этапа (сек) |
| strictNarrowAislePlacement | bool | **true** | **Режим размещения узкопроходных паллет** (ширина И глубина ≤ 1200 мм). `true` — только в узкопроходные секции (`narrowAisle=true`), если все заняты → `notPlaced` с причиной `NARROW_AISLE_MISMATCH`. `false` — узкопроходные секции проверяются первыми (приоритет), но если заняты → размещается в широкопроходную секцию. |
| **twoStageReslot** | **bool** | **false** | **🆕 Двухэтапный режим** — только для mode="place". ЭТАП 1: размещение без реслота (allowReslot=False), ЭТАП 2: реслот не размещённых (allowReslot=True). Игнорирует `allowReslot` на ЭТАПЕ 1. **Рекомендуется для холодного старта (>1000 паллет).** |
| **twoStageReslotMaxReslotPercent** | **float** | **10.0** | **🆕** maxReslotPercent для ЭТАПА 2 (если twoStageReslot=True) |
| **twoStageReslotTimeLimitSeconds** | **int** | **120** | **🆕** timeLimitSeconds для ЭТАПА 2 (если twoStageReslot=True) |
| **solverType** | **string** | **"cp_sat"** | **🆕 Тип солвера:** `"cp_sat"` — OR-Tools CP-SAT (макс. качество), `"numpy"` — NumPy+SciPy type-level greedy (быстрее), `"hybrid-v3"` — BFD+Chain-Swap (быстрый), `"hybrid-v5"` — BFD hints + CP-SAT (баланс). См. [Выбор солвера](#выбор-солвера) |

#### Выбор солвера

| Солвер | `solverType` | Качество (S7) | Время (S7) | Когда использовать |
|--------|-------------|--------------|-----------|-------------------|
| **Hybrid V5** | `"hybrid-v5"` | **3238/3406 (95.1%)** | 368s | Максимальное качество, 0 ошибок |
| **CP-SAT** | `"cp_sat"` | 3332/3406 (97.8%) | 253s | Макс. размещение (допускает WIDTH_OVERFLOW) |
| **NumPy** | `"numpy"` | 3218/3406 (94.5%) | 57s | Скорость, инкрементальное размещение |
| **Hybrid V3** | `"hybrid-v3"` | 3215/3406 (94.4%) | 4.1s | Макс. скорость, BFD+Chain-Swap, 0 ошибок |

**Hybrid V5 солвер** (aggregate CP-SAT + V3 reslot):
- Запускает агрегированную CP-SAT модель Y[тип, бакет] для глобальной оптимизации
- Дезагрегация + residual passes (exact CP-SAT, consolidation, virtual reslot)
- Если `allowReslot=true` и есть существующие паллеты — chain-swap реслот неразмещённых
- Защита от WIDTH_OVERFLOW в финальной сборке ответа
- Рекомендуется для холодного старта когда время не критично

**NumPy солвер** (type-level greedy + two-stage reslot):
- Группирует паллеты по типоразмерам (41 тип)
- Группирует секции по бакетам (~22 типа)
- Жадное распределение на уровне типов → дезагрегация
- LP-релаксация (scipy.linprog) + округление → greedy добор
- Консолидация + виртуальный реслот

**CP-SAT солвер** (текущий, по умолчанию):
- Агрегированная модель Y[тип, бакет] → CP-SAT → точное целочисленное решение
- Эталонное качество, но 4.4× медленнее

Пример с NumPy:
```json
{
  "settings": {
    "solverType": "numpy",
    "twoStageReslot": true,
    "twoStageReslotMaxReslotPercent": 40,
    "maxOperations": 5000
  }
}
```

#### maxOperations — что это?

`maxOperations` — лимит количества операций в **плане размещения** (операции PUT + MOVE в результате).

- **500 паллет** → ~500 операций (в худшем случае каждая паллета = 1 операция PUT)
- **3406 паллет** → ~3400 операций (плюс MOVE для реслота)

**Как выбирать:**
```
maxOperations ≥ количество новых паллет + 10% от existing (на реслот)
```

**Типичные значения:**
| Сценарий | maxOperations | Примечание |
|----------|-------------|-----------|
| Холодный старт 3406 паллет | 5000 | 3406 PUT + 1594 MOVE (10% от 1530 секций) |
| Инкрементальное размещение 200 паллет | 1000 | 200 PUT + 800 MOVE резерв |
| Уплотнение (mode=compact) | 500 | Только MOVE, нет PUT |
| Тест/debug | 300 | Минимум, для быстрого прогона |

**Рекомендации:**
- **Тест:** allowReslot=false, time=30, maxOperations=300, strictNarrowAislePlacement=true, solverType="cp_sat"
- **Быстрый:** solverType="hybrid-v3", maxOperations=5000 (4с, 94.4%)
- **Качество:** solverType="hybrid-v5", allowReslot=true, time=300, maxOperations=5000 (368с, 95.1%)
- **Рабочий:** solverType="cp_sat", allowReslot=false, time=120, maxOperations=5000, strictNarrowAislePlacement=true
- **Холодный старт с реслотом:** solverType="cp_sat", twoStageReslot=true, maxOperations=5000
- **Холодный старт быстро:** solverType="numpy", twoStageReslot=true, twoStageReslotMaxReslotPercent=40, maxOperations=5000
- **Полная оптимизация:** solverType="cp_sat", allowReslot=true (20%), time=300, maxOperations=5000

### OptimizationResponse

| Поле | Тип | Описание |
|------|-----|---------|
| solverStatus | enum | OPTIMAL / FEASIBLE / TIME_LIMIT / INFEASIBLE |
| placementStatus | enum | FULL / PARTIAL / NONE |
| score | float | Чем выше — тем лучше |
| executionTimeSeconds | float | Время расчёта на сервере |
| operations | array | Операции для выполнения |
| notPlaced | array | Не размещённые паллеты |
| metrics | object | Статистика |

### Operation (элемент operations)

```json
{
  "pallet": "uuid",
  "operation": "PUT",
  "oldAddress": null,
  "newAddress": "address-uuid",
  "sequence": 1
}
```

**operation:**
- `PUT` — разместить новую паллету
- `MOVE` — переставить существующую

### NotPlaced (элемент notPlaced)

```json
{
  "pallet": "uuid",
  "reason": "NARROW_AISLE_MISMATCH",
  "details": {"checkedSections": 12, "availableSections": 0}
}
```

**reason — возможные значения:**

| Код | Условие |
|-----|---------|
| `NARROW_AISLE_MISMATCH` | Паллета узкопроходная (ширина И глубина ≤ 1200 мм), все секции `narrowAisle=false`, а `strictNarrowAislePlacement=true` |
| `HEIGHT_LIMIT` | Высота паллеты больше высоты всех секций |
| `DEPTH_LIMIT` | Глубина паллеты больше глубины всех секций |
| `LIFT_LIMIT` | Вес паллеты больше `max_lift_weight` всех секций |
| `MAX_PALLET_SIZE_LIMIT` | Ширина или глубина паллеты больше `max_widthPallet`/`max_depthPallet` секции |
| `WEIGHT_LIMIT` | Суммарный вес в секции превысит `typeSize_weight` |
| `NO_SPACE` | Нет свободного места по размерам или количеству паллет |
| `RESLOT_LIMIT` | Физически место есть, но занято под реслот-квоту (`maxReslotPercent`) |

---

## Примеры — curl

### 1️⃣ Синхронный запрос (одноэтапный)

```bash
curl -X POST http://localhost:8010/api/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "optimizationId": "test-001",
    "mode": "place",
    "occupancy": [
      {
        "section_id": "sec-uuid-1",
        "section_code": "01-01-01",
        "rack_id": "rack-uuid",
        "rack_code": 1,
        "floor": 1,
        "accessLevel": 1,
        "typeSize_width": 1200,
        "typeSize_height": 2000,
        "typeSize_depth": 800,
        "typeSize_weight": 3000,
        "max_pallets": 3,
        "address1": "",
        "pallet1_id": ""
      }
    ],
    "newPallets": [
      {
        "id": "pallet-uuid-1",
        "width": 800,
        "height": 1500,
        "depth": 600,
        "weight": 400,
        "accessLevel": 1
      }
    ],
    "settings": {
      "allowReslot": false,
      "timeLimitSeconds": 60,
      "maxOperations": 300
    }
  }'
```

### 2️⃣ Холодный старт с двухэтапным подходом (РЕКОМЕНДУЕТСЯ для >1000 паллет)

```bash
curl -X POST http://localhost:8010/api/optimize \
  -H "Content-Type: application/json" \
  -d '{
    "optimizationId": "cold-start-001",
    "mode": "place",
    "occupancy": [...],
    "newPallets": [...3406 паллет...],
    "settings": {
      "twoStageReslot": true,
      "timeLimitSeconds": 180,
      "twoStageReslotTimeLimitSeconds": 120,
      "twoStageReslotMaxReslotPercent": 10.0,
      "maxOperations": 5000,
      "strictNarrowAislePlacement": true
    }
  }'
```

**Результат:**
```
ЭТАП 1 (180s): размещение без реслота → 3241/3406
ЭТАП 2 (120s): реслот не размещённых → +91 дополнительно
ИТОГО: 3333/3406 (97.8%) за 275.9s ✅
```

### 3️⃣ Асинхронный запрос (для больших задач)

```bash
# 1. Запуск в фоне
curl -X POST http://localhost:8010/api/optimize/async \
  -H "Content-Type: application/json" \
  -d '{
    "optimizationId": "async-large-001",
    "mode": "place",
    "occupancy": [...],
    "newPallets": [...2690 паллет...],
    "settings": {
      "twoStageReslot": true,
      "timeLimitSeconds": 240,
      "twoStageReslotTimeLimitSeconds": 180,
      "maxOperations": 10000
    }
  }'

# Response: {"optimizationId": "async-large-001", "status": "PENDING", "progress": 0}

# 2. Проверка статуса каждые 30 секунд
curl http://localhost:8010/api/optimization/async-large-001
# {"optimizationId": "async-large-001", "status": "RUNNING", "progress": 25}

# 3. Получение результата (когда status = COMPLETED)
curl http://localhost:8010/api/optimization/async-large-001/result
```

---

## Примеры — 1С BSL

### 🟢 Холодный старт (рекомендуется для >1000 паллет) — с двухэтапным подходом

**ЭТАП 1:** размещение максимально (180s, без реслота)  
**ЭТАП 2:** реслот остатков (120s, с разрешением двигать 10% паллет)

```bsl
Процедура ОптимизироватьРазмещениеПаллетХолодныйСтарт(Склад)
    
    СкладGUID = Строка(Склад.УникальныйИдентификатор());
    
    // ========================================================================
    // 1. ПОЛУЧИТЬ SNAPSHOT СКЛАДА
    // ========================================================================
    
    Сообщить("Получение состояния склада...");
    
    ПараметрыSnapshot = Новый Структура("warehouse", СкладGUID);
    ОтветSnapshot = Лико_WMS_Сервер.WMS_GetOccupancy(ПараметрыSnapshot);
    
    Если НЕ ОтветSnapshot.Успешно Тогда
        Сообщить("Ошибка: " + ОтветSnapshot.Ошибка);
        Возврат;
    КонецЕсли;
    
    ЧтениеJSON = Новый ЧтениеJSON;
    ЧтениеJSON.УстановитьСтроку(ОтветSnapshot.Тело);
    Occupancy = ПрочитатьJSON(ЧтениеJSON);
    ЧтениеJSON.Закрыть();
    
    Сообщить("Секций: " + Формат(Occupancy.Количество(), "ЧГ=0"));
    
    // ========================================================================
    // 2. ПОЛУЧИТЬ ПАЛЛЕТЫ С ПОЛА
    // ========================================================================
    
    Запрос = Новый Запрос;
    Запрос.Текст =
    "ВЫБРАТЬ
    |    СТРОКА(Остатки.Паллет.УникальныйИдентификатор()) КАК id,
    |    Параметры.Типоразмер.Ширина КАК width,
    |    Параметры.Типоразмер.Высота КАК height,
    |    Параметры.Типоразмер.Глубина КАК depth,
    |    Параметры.Типоразмер.Вес КАК weight,
    |    1 КАК accessLevel
    |ИЗ
    |    РегистрНакопления.Лико_ПаллетыВСекциях.Остатки(,
    |        Секция = ЗНАЧЕНИЕ(Справочник.Лико_СкладскиеСекции.ПустаяСсылка)
    |        И Адрес.Владелец = &Склад
    |    ) КАК Остатки
    |        ВНУТРЕННЕЕ СОЕДИНЕНИЕ РегистрСведений.Лико_ПараметрыПаллет.СрезПоследних КАК Параметры
    |        ПО Остатки.Паллет = Параметры.Паллет
    |ГДЕ
    |    Параметры.Типоразмер.Высота > 0
    |    И Параметры.Типоразмер.Ширина > 0
    |    И Параметры.Типоразмер.Глубина > 0";
    
    Запрос.УстановитьПараметр("Склад", Склад);
    МассивПаллет = Лико_HTTP_Сервер.ТзВМассивСтруктур(Запрос.Выполнить().Выгрузить());
    
    Сообщить("Паллет на полу: " + Формат(МассивПаллет.Количество(), "ЧГ=0"));
    
    Если МассивПаллет.Количество() = 0 Тогда
        Сообщить("Нечего размещать.");
        Возврат;
    КонецЕсли;
    
    // ========================================================================
    // 3. ЗАПРОС К ОПТИМИЗАТОРУ С ДВУХЭТАПНЫМ ПОДХОДОМ
    // ========================================================================
    
    ЗапросОптимизации = Новый Структура;
    ЗапросОптимизации.Вставить("optimizationId", Строка(Новый УникальныйИдентификатор()));
    ЗапросОптимизации.Вставить("mode", "place");
    ЗапросОптимизации.Вставить("occupancy", Occupancy);
    ЗапросОптимизации.Вставить("newPallets", МассивПаллет);
    
    // ДВУХЭТАПНЫЕ НАСТРОЙКИ
    Settings = Новый Структура;
    Settings.Вставить("twoStageReslot", Истина);  // Включаем двухэтапный режим
    Settings.Вставить("maxOperations", 5000);      // Хватит для 3406 паллет + 10% реслот
    Settings.Вставить("timeLimitSeconds", 180);    // ЭТАП 1: размещение (180s)
    Settings.Вставить("twoStageReslotTimeLimitSeconds", 120);  // ЭТАП 2: реслот (120s)
    Settings.Вставить("twoStageReslotMaxReslotPercent", 10.0); // ЭТАП 2: двигаем до 10%
    Settings.Вставить("strictNarrowAislePlacement", Истина);   // Узкопроходные = узкопроходные секции
    ЗапросОптимизации.Вставить("settings", Settings);
    
    Сообщить("Отправка в оптимизатор...");
    НачалоВремя = ТекущаяДата();
    
    Попытка
        HTTPСоединение = Новый HTTPСоединение("localhost", 8010);
        HTTPЗапрос = Новый HTTPЗапрос("/api/optimize");
        HTTPЗапрос.Заголовки.Вставить("Content-Type", "application/json; charset=utf-8");
        HTTPЗапрос.УстановитьТелоИзСтроки(
            Лико_HTTP_Сервер.СтруктураВJSON(ЗапросОптимизации),
            КодировкаТекста.UTF8
        );
        
        HTTPОтвет = HTTPСоединение.ВызватьHTTPМетод("POST", HTTPЗапрос);
        
        Если HTTPОтвет.КодСостояния <> 200 Тогда
            Сообщить("Ошибка HTTP " + Формат(HTTPОтвет.КодСостояния, "ЧГ=0"));
            Сообщить(HTTPОтвет.ПолучитьТелоКакСтроку());
            Возврат;
        КонецЕсли;
        
        // Парсинг результата
        ЧтениеРезультата = Новый ЧтениеJSON;
        ЧтениеРезультата.УстановитьСтроку(HTTPОтвет.ПолучитьТелоКакСтроку());
        Результат = ПрочитатьJSON(ЧтениеРезультата);
        ЧтениеРезультата.Закрыть();
        
        ВремяВыполнения = ТекущаяДата() - НачалоВремя;
        
        // Вывод результата
        Сообщить("=============================================================");
        Сообщить("РЕЗУЛЬТАТ:");
        Сообщить("=============================================================");
        Сообщить("Solver: " + Результат.solverStatus);
        Сообщить("Placement: " + Результат.placementStatus);
        Сообщить("Score: " + Формат(Результат.score, "ЧГ=0"));
        Сообщить("Server time: " + Формат(Результат.executionTimeSeconds, "ЧЦ=10; ЧДЦ=2") + " сек");
        Сообщить("Total time: " + Формат(ВремяВыполнения, "ЧЦ=10; ЧДЦ=2") + " сек");
        Сообщить("-------------------------------------------------------------");
        Сообщить("Размещено: " + Формат(Результат.metrics.placedPallets, "ЧГ=0"));
        Сообщить("НЕ размещено: " + Формат(Результат.metrics.notPlacedPallets, "ЧГ=0"));
        Сообщить("PUT операций: " + Формат(Результат.operations.Количество(), "ЧГ=0"));
        Сообщить("=============================================================");
        
        // ====================================================================
        // 4. ВЫПОЛНЕНИЕ ПЛАНА (опционально)
        // ====================================================================
        
        Если Результат.metrics.placedPallets > 0 Тогда
            
            Ответ = Вопрос("Выполнить план размещения?", РежимДиалогаВопрос.ДаНет);
            Если Ответ = КодВозвратаДиалога.Нет Тогда
                Возврат;
            КонецЕсли;
            
            МассивPlacements = Новый Массив;
            
            Для Каждого Op Из Результат.operations Цикл
                Если Op.operation = "PUT" Тогда
                    Placement = Новый Структура;
                    Placement.Вставить("pallet", Op.pallet);
                    Placement.Вставить("address", Op.newAddress);
                    МассивPlacements.Добавить(Placement);
                КонецЕсли;
            КонецЦикла;
            
            ПараметрыВыполнения = Новый Структура;
            ПараметрыВыполнения.Вставить("warehouse", СкладGUID);
            ПараметрыВыполнения.Вставить("placements", МассивPlacements);
            ПараметрыВыполнения.Вставить("rearrangements", Новый Массив);
            
            ОтветВыполнения = Лико_WMS_Сервер.WMS_PlacePallets(ПараметрыВыполнения);
            
            Если ОтветВыполнения.Успешно Тогда
                Сообщить("✓ План выполнен!");
                Сообщить("Создано документов: " + Формат(ОтветВыполнения.Результат.documentsCreated, "ЧГ=0"));
            Иначе
                Сообщить("✗ Ошибка выполнения: " + ОтветВыполнения.Ошибка);
            КонецЕсли;
            
        КонецЕсли;
        
    Исключение
        Сообщить("ОШИБКА: " + ОписаниеОшибки());
    КонецПопытки;
    
КонецПроцедуры
```

### Асинхронный режим из 1С

```bsl
Процедура ОптимизироватьАсинхронно(Склад, МассивПаллет, Occupancy)
    
    // 1. Запуск асинхронной оптимизации
    OptID = Строка(Новый УникальныйИдентификатор());
    
    ЗапросОптимизации = Новый Структура;
    ЗапросОптимизации.Вставить("optimizationId", OptID);
    ЗапросОптимизации.Вставить("mode", "place");
    ЗапросОптимизации.Вставить("occupancy", Occupancy);
    ЗапросОптимизации.Вставить("newPallets", МассивПаллет);
    
    Settings = Новый Структура;
    Settings.Вставить("timeLimitSeconds", 600);
    ЗапросОптимизации.Вставить("settings", Settings);
    
    HTTPСоединение = Новый HTTPСоединение("localhost", 8010);
    HTTPЗапрос = Новый HTTPЗапрос("/api/optimize/async");
    HTTPЗапрос.Заголовки.Вставить("Content-Type", "application/json");
    HTTPЗапрос.УстановитьТелоИзСтроки(
        Лико_HTTP_Сервер.СтруктураВJSON(ЗапросОптимизации),
        КодировкаТекста.UTF8
    );
    
    HTTPОтвет = HTTPСоединение.ВызватьHTTPМетод("POST", HTTPЗапрос);
    
    Если HTTPОтвет.КодСостояния <> 202 Тогда
        Сообщить("Ошибка запуска: " + HTTPОтвет.ПолучитьТелоКакСтроку());
        Возврат;
    КонецЕсли;
    
    Сообщить("✓ Задача запущена: " + OptID);
    Сообщить("Ожидание результата...");
    
    // 2. Опрос статуса
    Пока Истина Цикл
        
        HTTPЗапросСтатус = Новый HTTPЗапрос("/api/optimization/" + OptID);
        HTTPОтветСтатус = HTTPСоединение.ВызватьHTTPМетод("GET", HTTPЗапросСтатус);
        
        ЧтениеСтатус = Новый ЧтениеJSON;
        ЧтениеСтатус.УстановитьСтроку(HTTPОтветСтатус.ПолучитьТелоКакСтроку());
        Статус = ПрочитатьJSON(ЧтениеСтатус);
        ЧтениеСтатус.Закрыть();
        
        Сообщить("Status: " + Статус.status);
        
        Если Статус.status = "COMPLETED" Тогда
            Прервать;
        ИначеЕсли Статус.status = "FAILED" Тогда
            Сообщить("Задача завершилась с ошибкой");
            Возврат;
        КонецЕсли;
        
        // Ждём 5 секунд
        СистемаВызовСервера.ПодождатьСекунд(5);
        
    КонецЦикла;
    
    // 3. Получение результата
    HTTPЗапросРезультат = Новый HTTPЗапрос("/api/optimization/" + OptID + "/result");
    HTTPОтветРезультат = HTTPСоединение.ВызватьHTTPМетод("GET", HTTPЗапросРезультат);
    
    ЧтениеРезультат = Новый ЧтениеJSON;
    ЧтениеРезультат.УстановитьСтроку(HTTPОтветРезультат.ПолучитьТелоКакСтроку());
    Результат = ПрочитатьJSON(ЧтениеРезультат);
    ЧтениеРезультат.Закрыть();
    
    Сообщить("✓ Готово!");
    Сообщить("Размещено: " + Формат(Результат.metrics.placedPallets, "ЧГ=0"));
    Сообщить("Score: " + Формат(Результат.score, "ЧГ=0"));
    
КонецПроцедуры
```

---

## Коды ошибок

| HTTP | Причина | Описание |
|------|---------|---------|
| 200 | OK | Успех |
| 202 | Accepted | Async задача принята |
| 404 | Not Found | Задача не найдена |
| 422 | Validation Error | Невалидные данные запроса |
| 500 | Internal Error | Ошибка солвера/сервера |

**Частые ошибки валидации (422):**

- `DUPLICATE_PALLET_IDS` — дубликаты в newPallets
- `INVALID_DIMENSIONS` — размеры ≤ 0
- `MODE_MISMATCH` — mode=compact + newPallets непустой
- `EMPTY_OCCUPANCY` — нет ни одной секции

---

## Выбор режима: sync vs async

| Критерий | Sync | Async |
|----------|------|-------|
| Паллет | < 500 | > 500 |
| Время | < 120s | > 120s |
| Клиент | Блокируется | Не блокируется |
| Реализация | Простая | Polling + retry |
| Use case | Рабочий процесс | Ночная оптимизация |

---

## Метрики и производительность

**Типичное время выполнения (Intel i7, 16GB RAM):**

| Паллет | Секций | allowReslot | Time Limit | Фактическое время |
|--------|--------|:-----------:|:----------:|:-----------------:|
| 50 | 200 | false | 30s | 2-5s |
| 200 | 800 | false | 120s | 10-25s |
| 500 | 2000 | false | 120s | 45-90s |
| 1000 | 4000 | false | 300s | 120-240s |
| 2690 | 8000+ | false | 600s | 400-600s |

**score:** абсолютное значение зависит от весов в `config/weights.json`. Используй для **сравнения** двух запусков на одинаковых данных.

---

## Интеграция в production

### 1. Настройка Docker для production

```yaml
# docker-compose.yml
services:
  wms-optimizer:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 4G
        reservations:
          cpus: '2'
          memory: 2G
    restart: always
    healthcheck:
      interval: 60s
      timeout: 30s
      retries: 5
```

### 2. Мониторинг

```bash
# Логи
docker-compose logs -f wms-optimizer | grep ERROR

# Статистика
docker stats wms-optimizer
```

### 3. Backup плана перед выполнением

```bsl
// Сохранить план в JSON перед WMS_PlacePallets
ПланJSON = Лико_HTTP_Сервер.СтруктураВJSON(Результат);
ЗаписатьФайл("d:\backup\plan_" + Формат(ТекущаяДата(), "ДФ=yyyyMMdd_HHmmss") + ".json", ПланJSON);
```

---

## FAQ

**Q: Можно ли запустить несколько оптимизаций параллельно?**  
A: Да, в async режиме. Каждая задача имеет свой optimizationId.

**Q: Что делать если solverStatus = TIME_LIMIT?**  
A: Это нормально для больших задач. Результат всё равно валидный — просто не гарантированно оптимальный. Если нужно лучше — увеличь timeLimitSeconds.

**Q: Почему некоторые паллеты в notPlaced?**  
A: Проверь `notPlaced[].reason`:
- `NARROW_AISLE_MISMATCH` — паллета узкопроходная (ширина И глубина ≤ 1200 мм), а все узкопроходные секции (`narrowAisle=true`) заняты. Передай `strictNarrowAislePlacement=false` чтобы разрешить размещение в широкопроходные секции как запасной вариант.
- `NO_SPACE` — нет подходящих свободных мест (размеры, количество)
- `LIFT_LIMIT`, `HEIGHT_LIMIT` и др. — физические ограничения секций.  
Подробности в `notPlaced[].details.checkedSections`.

**Q: Как сравнить два запуска?**  
A: По `score` (выше = лучше) и `metrics.usedSections` (меньше = компактнее).

---

**Версия документации:** 1.0  
**Дата:** 2026-07-21  
**Сервис:** wms_optimizer v1.0.0
