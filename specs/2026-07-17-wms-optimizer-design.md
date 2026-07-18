# WMS Warehouse Loading Optimizer

> Веб-приложение для оптимизации размещения паллет на складе адресного хранения.
> Стек: Docker, Svelte 5 + Tailwind CSS, Python FastAPI, PostgreSQL, 1С HTTP-сервис.

## Оглавление

1. [Архитектура](#архитектура)
2. [1С HTTP API (7 эндпоинтов)](#1с-http-api)
3. [Python Backend](#python-backend)
4. [PostgreSQL](#postgresql)
5. [Svelte Frontend](#svelte-frontend)
6. [Визуализация](#визуализация)
7. [Алгоритм размещения](#алгоритм-размещения)

---

## Архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│                       docker-compose                             │
│                                                                  │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────┐   │
│  │   nginx      │    │   FastAPI :8080  │    │  PostgreSQL  │   │
│  │   + Svelte   │◄──►│                  │◄──►│  :5432       │   │
│  │   :80        │    │  /api/warehouses │    │              │   │
│  │              │    │  /api/racks      │    │  connections │   │
│  │              │    │  /api/occupancy  │    │              │   │
│  │              │    │  /api/floor      │    └──────────────┘   │
│  │              │    │  /api/optimize   │                       │
│  │              │    │  /api/validate   │                       │
│  │              │    │  /api/move       │                       │
│  │              │    │  /api/connections│                       │
│  └──────────────┘    └────────┬─────────┘                       │
│                               │ HTTP                             │
└───────────────────────────────┼──────────────────────────────────┘
                                ▼
                     ┌────────────────────┐
                     │  1С Liko_Rest      │
                     │  /hs/Liko_Rest     │
                     │  7 ProcName: WMS_* │
                     └────────────────────┘
```

**Контейнеры (3):**

| Контейнер | Содержимое | Порт |
|---|---|---|
| `wms-frontend` | nginx + собранный Svelte SPA | 80 |
| `wms-backend` | Python 3 + FastAPI + uvicorn | 8080 |
| `wms-db` | PostgreSQL 16 | 5432 |

**nginx routing:**
- `/` → Svelte SPA (index.html)
- `/api/*` → прокси на `wms-backend:8080`

---

## 1С HTTP API

Все через существующий `POST /hs/Liko_Rest` с полем `ProcName`.
Добавляются в существующий HTTP-сервис `Liko_Rest`.
BSL-код размещается в общем модуле расширения `Лико_WMS_Сервер`.

### WMS_GetWarehouses — список складов

```
→ { "ProcName": "WMS_GetWarehouses" }

← { "warehouses": [{
      "id": "UUID", "name": "ЛК Высотный", "code": "000000001"
    }]
  }
```

**BSL:** запрос к `Справочник.Склады`, только где есть `Лико_Стеллажи`.

### WMS_GetRacks — топология стеллажей

```
→ { "ProcName": "WMS_GetRacks", "warehouse": "UUID" }

← { "racks": [{
      "id": "UUID", "name": "Стеллаж 1", "code": "1",
      "number": 1,                        // НомерСтеллажа
      "narrowAisle": false,               // Узкопроходный
      "color": "#E8C98A",                 // из ДопНастройки

      // Ограничения стеллажа (0 = не ограничено)
      "minPalletWidth": 0, "maxPalletWidth": 1350,
      "minPalletDepth": 0, "maxPalletDepth": 1100,

      "sectionsCount": 17,
      "cellsPerSection": 3,
      "floorZonesCount": 5,
      "accessTime": 120,

      "floors": [{
        "number": 1,
        "typeSize": {
          "id": "UUID", "height": 2340, "width": 2700,
          "depth": 1100, "weight": 0, "unlimitedWeight": true
        },
        "minDepthUnlimited": true,        // МинимальнаяГлубинаНеОграничена
        "maxDepthUnlimited": true,        // МаксимальнаяГлубинаНеОграничена
        "maxLiftWeight": 2000,            // МаксимальныйВесПодъёмаНаЭтаж
        "heightClearance": 100,           // Зазор (по высоте), мм
        "beamHeight": 120,                // ВысотаБалки, мм
        "heightFromFloor": 200,           // ВысотаОтПола, мм
        "heightFromFloorFixed": true,     // ВысотаОтПолаФиксирована
        "widthClearance": 50              // ЗазорПоШирине, мм
      }]
    }]
  }
```

**BSL:** `Справочник.Лико_Стеллажи` + ТЧ `Этажи` + `Лико_ТипоразмерыСкладскихСекций`.

### WMS_GetOccupancy — занятость секций

```
→ { "ProcName": "WMS_GetOccupancy", "warehouse": "UUID" }

← { "sections": [{
      "id": "UUID", "code": "Р1-М(1-2-3)-Э1",
      "rack": "UUID", "floor": 1,
      "accessLevel": 1,                  // 1=ближняя, 2=дальняя
      "restricted": false,               // ЗапретРазмещения
      "typeSize": { "width": 2700, ... },
      "addresses": [
        { "address": "UUID", "addressCode": "Р1М1Э1",
          "pallet": "UUID или null", "palletCode": "P-00123",
          "width": 800, "height": 1200, "depth": 1100, "weight": 500 },
        { "address": "UUID", "addressCode": "Р1М2Э1", "pallet": null },
        { "address": "UUID", "addressCode": "Р1М3Э1", "pallet": "UUID" }
      ]
    }],
  "summary": {
    "totalSections": 1403, "totalAddresses": 4209,
    "occupiedAddresses": 2847, "freeAddresses": 1362,
    "occupancyPercent": 67.6
  }
}
```

**BSL:** `Лико_ПаллетыВСекциях.Остатки` + `Лико_ПараметрыПаллет.СрезПоследних`.

### WMS_GetFloor — паллеты на полу

```
→ { "ProcName": "WMS_GetFloor", "warehouse": "UUID" }

← { "floorPallets": [{
      "rack": "UUID или null",           // null = общая зона без стеллажа
      "address": "UUID",
      "code": "ПОЛ-Приёмка-1",
      "pallet": { "id": "UUID", "code": "P-00789",
                  "width": 800, "height": 1200, "depth": 1100, "weight": 500 }
    }]
  }
```

**BSL:** `Лико_Стеллажи.ЗоныПриемкиОтгрузки` + паллеты без секции из `Лико_ПаллетыВСекциях`.

### WMS_PlacePallets — расчёт размещения (план)

```
→ { "ProcName": "WMS_PlacePallets",
    "warehouse": "UUID",
    "pallets": [
      { "id": "UUID", "width": 800, "height": 1200, "depth": 1100, "weight": 500 }
    ],
    "options": {
      "mode": "newOnly",                 // "newOnly" | "fullReslot"
      "fillExistingFirst": true,
      "maxPalletsPerSection": 3,
      "respectAccessLevel": false
    }
  }

← { "placements": [{
      "pallet": "UUID", "section": "UUID", "sectionCode": "Р1-М(1-2-3)-Э1",
      "address": "UUID", "addressCode": "Р1М1Э1",
      "action": "place",                 // "keep" | "move" | "place"
      "priority": 1                      // 1=добил до 3, 2=до 2, 3=один, 4=новая
    }],
    "unplaced": [{ "id": "UUID", "reason": "..." }],
    "stats": {
      "total": 20, "placed": 18, "perfectSections": 5,
      "sectionsUsed": 12, "movesRequired": 0
    }
  }
```

**BSL:** алгоритм «рюкзак» — сортировка по убыванию ширины, фаза добивки существующих секций, фаза новых секций, проверка зазора `(N+1)×ЗазорПоШирине`.

### WMS_ValidatePlacement — проверка ячейки

```
→ { "ProcName": "WMS_ValidatePlacement",
    "warehouse": "UUID", "cell": "UUID ячейки", "pallet": "UUID паллета" }

← { "valid": true, "errors": [] }

← { "valid": false,
    "errors": [
      { "code": "WEIGHT_EXCEEDED", "message": "Превышена грузоподъёмность" },
      { "code": "WIDTH_CONFLICT",  "message": "Блокирует соседние адреса" }
    ]
  }
```

**BSL:** вызывает `Лико_СкладскиеСекции.ОшибкиРазмещенияВАдрес(Ячейка, Паллет)`.

### WMS_MovePallet — переместить паллет

```
→ { "ProcName": "WMS_MovePallet",
    "warehouse": "UUID", "pallet": "UUID", "targetCell": "UUID" }

← { "ok": true, "document": "UUID ПеремещениеПаллета2_0",
    "message": "Паллет P-00123 → Р1М2Э1" }
```

**BSL:** создаёт и проводит `Документ.Лико_ПеремещениеПаллета2_0`.

---

## Python Backend

FastAPI-приложение в `services/wms/`.

### Структура проекта

```
services/wms/
├── app.py                  # FastAPI приложение
├── requirements.txt        # fastapi, uvicorn, httpx, asyncpg, pydantic
├── Dockerfile
├── routers/
│   ├── wms.py              # /api/warehouses, /api/racks, /api/occupancy, /api/floor
│   ├── optimize.py         # /api/optimize (алгоритм)
│   ├── validate.py         # /api/validate
│   ├── move.py              # /api/move
│   └── connections.py      # /api/connections (CRUD)
├── services/
│   ├── client_1c.py        # HTTP-клиент к 1С Liko_Rest
│   ├── optimizer.py        # Алгоритм размещения (рюкзак)
│   └── calculator.py       # Расчёт зазоров, остатков, процентов
├── db/
│   ├── connection.py       # Подключение к PostgreSQL
│   └── queries.py          # SQL-запросы
├── models/
│   ├── warehouse.py        # Pydantic модели
│   ├── rack.py
│   ├── section.py
│   └── pallet.py
└── config.py               # Настройки (из env vars)
```

### API эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/warehouses` | Список складов (прокси в 1С) |
| POST | `/api/racks` | Топология стеллажей (прокси + обогащение) |
| POST | `/api/occupancy` | Занятость секций (прокси + расчёт %) |
| POST | `/api/floor` | Паллеты на полу (прокси) |
| POST | `/api/optimize` | Запуск алгоритма размещения |
| POST | `/api/validate` | Проверка: можно ли поставить паллет |
| POST | `/api/move` | Переместить паллет |
| POST | `/api/connections` | Список подключений к 1С |
| POST | `/api/connections/add` | Добавить подключение |
| POST | `/api/connections/update` | Обновить подключение |
| POST | `/api/connections/delete` | Удалить подключение |
| POST | `/api/connections/activate` | Сделать активным |

### Кэширование

- `WMS_GetWarehouses` → кэш 24 часа
- `WMS_GetRacks` → кэш 1 час (топология)
- `WMS_GetOccupancy` → без кэша (меняется при каждом перемещении)
- `WMS_GetFloor` → без кэша

---

## PostgreSQL

База: `wms`. Таблица `connections`:

```sql
CREATE TABLE connections (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    base_url    VARCHAR(255) NOT NULL,
    username    VARCHAR(100),
    password    VARCHAR(255),
    is_active   BOOLEAN DEFAULT false,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

Все WMS-запросы используют строку где `is_active = true`.

---

## Svelte Frontend

### Дерево компонентов

```
App.svelte
├── Header.svelte
│   ├── ConnectionSelector.svelte    // выбор базы 1С
│   ├── WarehouseSelector.svelte     // выбор склада
│   └── ZoomControls.svelte          // масштаб

├── MainLayout.svelte
│   ├── RackContainer.svelte         // левая панель 70%
│   │   ├── RackTabs.svelte          // вкладки стеллажей
│   │   ├── RackFrontView.svelte     // вид спереди
│   │   │   └── FloorRow.svelte
│   │   │       ├── FloorSide.svelte
│   │   │       ├── SectionCell.svelte
│   │   │       │   ├── AddressSlot.svelte    // draggable + validate
│   │   │       │   └── GapIndicator.svelte
│   │   │       └── RackPost.svelte
│   │   └── RackSideView.svelte      // вид сбоку
│   │       ├── SideFloor.svelte
│   │       └── SideBeam.svelte
│   │
│   └── SidePanel.svelte             // правая панель 30%
│       ├── FilterPanel.svelte
│       ├── UnplacedList.svelte
│       └── DetailPanel.svelte

└── StatsBar.svelte                  // нижняя панель
```

### Состояния и stores

Svelte stores (reactive state):

| Store | Содержит |
|---|---|
| `activeConnection` | Текущее подключение к 1С |
| `activeWarehouse` | Выбранный склад |
| `racks` | Данные стеллажей (из WMS_GetRacks) |
| `sections` | Данные секций с паллетами (из WMS_GetOccupancy) |
| `floorPallets` | Паллеты на полу (из WMS_GetFloor) |
| `placementPlan` | Результат WMS_PlacePallets |
| `filters` | Текущие фильтры (стеллаж, заполненность, поиск) |
| `selectedItem` | Выбранная секция или паллет (для DetailPanel) |

### Интерактив

| Действие | Поведение |
|---|---|
| Наведение на ячейку | Tooltip: адрес, ширина, остаток |
| Наведение на паллет | Tooltip: код, габариты, вес |
| Клик на паллет | DetailPanel → состав паллета |
| Клик на секцию | DetailPanel → параметры секции |
| Двойной клик на паллет | PalletComposition (номенклатура, серии) |
| Drag паллета | Валидация над ячейками (зелёный/красный/оранжевый) |
| Drop паллета | WMS_MovePallet → обновление occupancy |
| Кнопка «Рассчитать» | WMS_PlacePallets → план в UnplacedList |

### Drag-and-drop

```
AddressSlot.svelte (на паллете):
  draggable="true"
  on:dragstart → запоминаем паллет

AddressSlot.svelte (на ячейке):
  on:dragover  → POST /api/validate { cell, pallet }
    ← valid:true  → зелёная рамка
    ← valid:false → красная рамка + tooltip с ошибкой
  on:dragleave → убираем рамку
  on:drop      → POST /api/move { pallet, targetCell }
    ← ok:true → обновляем occupancy
```

### Фильтры

| Фильтр | Значения |
|---|---|
| По стеллажу | Галочки: Стеллаж 1-9 |
| По заполненности | Все / Свободные / Частично занятые / Полные |
| Поиск паллета | Поле ввода кода, подсветка найденного |

---

## Визуализация

### Вид спереди (фронтальный) — RackFrontView

Один стеллаж = одна "стена" из секций. Каждый этаж — горизонтальный ряд:

```
 ┌──────┬──────┬──────┬──────┬──────┐
 │ Сек1  │ Сек2  │ Сек3  │ ...  │Сек17  │ Этаж 9
 │▓▓▓ ▓▓▓│      │▓▓▓   │      │▓▓▓ ░░│
 ├──────┼──────┼──────┼──────┼──────┤
 │ Сек1  │ Сек2  │ Сек3  │ ...  │Сек17  │ Этаж 8
 │▓▓▓    │▓▓▓ ▓▓▓│▓▓▓ ▓▓▓│     │      │
 ├──────┼──────┼──────┼──────┼──────┤
 ...
 ├──────┼──────┼──────┼──────┼──────┤
 │ Сек1  │ Сек2  │ Сек3  │ ...  │Сек17  │ Этаж 1 (пол)
 │▓▓▓ ▓▓▓│      │▓▓▓ ▓▓▓│     │▓▓▓    │
 └──────┴──────┴──────┴──────┴──────┘
  ▓▓▓ = паллет   ░░ = запрет размещения
```

Секция = прямоугольник, разделённый на 3 вертикальных адреса. Ширина секции пропорциональна `typeSize.width`. Паллеты закрашены цветом, свободные адреса — зелёный фон.

Левая колонка (FloorSide): № этажа, высота секции, % загрузки этажа.

### Вид сбоку (профиль) — RackSideView

Один стеллаж в разрезе — видны балки, зазоры, глубина паллета:

```
Балка ═══════════════════
       │  Паллет (глуб. 1100)  │ ← зазор по высоте
Балка ═══════════════════
       │  Паллет (глуб. 1100)  │
Балка ═══════════════════
       ...
─────── ПОЛ ──────────────
       │← ВысотаОтПола →│
```

Параметры отрисовки берутся из `WMS_GetRacks.floors[]`:
- `beamHeight` → толщина линии балки
- `heightClearance` → пространство над паллетом
- `heightFromFloor` → расстояние от пола до первой балки

---

## Алгоритм размещения (рюкзак)

Реализован в Python (`services/wms/services/optimizer.py`) и/или BSL (`Лико_WMS_Сервер`).

### Псевдокод

```
Функция РазместитьПартию(склад, паллеты):

  1. ЗАГРУЗИТЬ состояние склада:
     - все секции с типоразмерами
     - текущие паллеты в адресах
     - для каждой секции: занятые адреса, остаток ширины/веса

  2. ОТСОРТИРОВАТЬ паллеты:
     - 3 группы: широкие(>W/2) → средние(>W/3) → узкие(≤W/3)
     - внутри группы — по убыванию ширины

  3. ШИРОКИЕ (>W/2, 3 адреса):
     для каждого: найти пустую секцию (все 3 адреса свободны,
                  габариты/вес проходят)

  4. СРЕДНИЕ + УЗКИЕ:
     для каждого паллета:
       ФАЗА А — ДОБИТЬ существующую:
         приоритет 1: секции с 2 паллетами → добить до 3
         приоритет 2: секции с 1 паллетом  → добить до 2
         проверка: сумма(ширин) + (N+1)×зазор ≤ ширина секции
                   + вес/высота/глубина ОК

       ФАЗА Б — НОВАЯ секция:
         если не добили → найти лучшую пустую секцию
         (best-fit по высоте, грузоподъёмности)

  5. ВЕРНУТЬ план:
     - placements: паллет → секция → адрес + priority
     - unplaced: что не влезло + причина
     - stats: total, placed, perfectSections, sectionsUsed
```

### Проверка комбинации (Фаза А)

```
Дано: секция 2700мм, уже стоит [900мм], зазорПоШирине=50мм
Новый паллет: 800мм

N_после = 2
требуемый_зазор = (2+1) × 50 = 150мм
сумма_ширин = 900 + 800 = 1700мм
всего = 1700 + 150 = 1850 ≤ 2700 ✓

→ проверяем адреса: средний свободен, соседи не блокируют
→ проверяем вес: (вес_секции - текущий_вес) ≥ вес_нового
→ проверяем высоту/глубину
→ OK → размещаем
```

### Формула монтажного зазора

Зазор = `(N_после_размещения + 1) × ЗазорПоШирине`

Где `ЗазорПоШирине` берётся из `Этажи.ЗазорПоШирине` стеллажа (обычно 50мм).

| N паллетов в секции | Зазор |
|---|---|
| 1 | (1+1)×50 = 100мм |
| 2 | (2+1)×50 = 150мм |
| 3 | (3+1)×50 = 200мм |

---

## Конфигурация (docker-compose)

```yaml
services:
  frontend:
    build: ./frontend
    ports: ["80:80"]
    depends_on: [backend]

  backend:
    build: ./services/wms
    ports: ["8080:8080"]
    environment:
      - DATABASE_URL=postgresql://wms:wms@db:5432/wms
      - CACHE_TTL_RACKS=3600
      - CACHE_TTL_WAREHOUSES=86400
    depends_on: [db]

  db:
    image: postgres:16
    environment:
      - POSTGRES_USER=wms
      - POSTGRES_PASSWORD=wms
      - POSTGRES_DB=wms
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

---

## Фазы реализации

| Фаза | Содержание | Результат |
|---|---|---|
| 1 | 1С: 7 ProcName в Liko_Rest + код в `Лико_WMS_Сервер` | Postman-ready API |
| 2 | Python: FastAPI + HTTP-клиент + алгоритм + PostgreSQL | Swagger UI |
| 3 | Svelte: визуализация + фильтры + интерактив + DnD | Веб-приложение локально |
| 4 | Docker: docker-compose + nginx + интеграция | `docker-compose up` |

---

## Связи

- [[ЗагрузкаСклада]] — существующая обработка 1С
- [[Лико_ПодобратьЯчейку]] — текущий алгоритм подбора
- [[Лико_Стеллажи]] — справочник стеллажей
- [[Лико_СкладскиеСекции]] — секции и адреса
- [[Лико_Паллеты2_0]] — паллеты
- [[Топология_Склада_Ликофлекс_Высотный]] — топология склада
- [[Liko_Rest]] — HTTP-сервис 1С
- [[Лико_HTTP_Сервер]] — HTTP-утилиты
- [[Лико_WMS_Сервер]] — новый модуль расширения
