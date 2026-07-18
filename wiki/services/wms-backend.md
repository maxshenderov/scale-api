# WMS Backend Service

> Python FastAPI приложение для управления складом: HTTP-прокси в 1С + Bin Packing optimizer.

## Назначение

Веб-приложение для WMS (Warehouse Management System):
- **Прокси в 1С** — 10 REST эндпоинтов к Лико_WMS_Сервер (получение состояния, перемещение паллет)
- **Optimizer** — Bin Packing алгоритм для размещения партии паллет (Best-Fit-Decreasing + reslotting)
- **Offline mode** — автоматический fallback на MOCK данные если 1С недоступна

## Endpoints (REST API)

### Warehouse Management (прокси в 1С)

| Метод | Путь | ProcName | Назначение |
|---|---|---|---|
| POST | `/api/warehouses` | WMS_GetWarehouses | Список складов с WMS |
| POST | `/api/racks` | WMS_GetRacks | Топология стеллажей (статичная) |
| POST | `/api/occupancy` | WMS_GetOccupancy | Текущее состояние адресов |
| POST | `/api/floor` | WMS_GetFloor | Паллеты на полу (приёмка/отгрузка) |
| POST | `/api/find-cell` | WMS_FindCell | Варианты мест для паллета |
| POST | `/api/validate` | WMS_ValidatePlacement | Проверка возможности размещения |
| POST | `/api/move` | WMS_MovePallet | Одиночное перемещение |
| POST | `/api/snapshot` | WMS_ExportSnapshot | Полный снимок склада |
| POST | `/api/health` | WMS_CheckConnection | Health-check API |
| POST | `/api/placements/execute` | WMS_ExecutePlacements | Пакетное размещение (массив) |

### Optimization (автономный, без 1С)

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/optimize` | Bin Packing: размещение партии паллет |
| POST | `/api/optimize/floors` | Подбор оптимальных высот этажей |
| GET | `/api/ping` | Проверка доступности сервиса |

### System (управление конфигурацией)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/connections` | Список сохранённых подключений к 1С |
| POST | `/connections` | Создать новое подключение |
| PUT | `/connections/{id}` | Обновить подключение |
| DELETE | `/connections/{id}` | Удалить подключение |
| POST | `/connections/{id}/activate` | Активировать подключение |

## Архитектура

```
app.py (FastAPI + CORS)
├── routers/
│   ├── warehouse.py — 10 эндпоинтов прокси + /api/optimize
│   ├── connections.py — управление подключениями (DB)
│   └── snapshots.py — сохранение снимков состояния
├── services/
│   ├── wms_client.py — HTTP-клиент к Liko_Rest (ProcName dispatcher)
│   ├── optimizer.py — Bin Packing: BFD + категории паллет + best-fit
│   ├── floor_optimizer.py — анализ использования этажей
│   └── mock_data.py — MOCK данные для offline
├── models/
│   └── schemas.py — Pydantic модели (PalletItem, PlacementResult, etc.)
├── db/
│   └── connection.py — AsyncPg/SQLAlchemy подключение к PostgreSQL
└── config.py — Pydantic Settings (env vars)
```

## Bin Packing Algorithm

**Phase 1 — Размещение новых паллет (Best-Fit-Decreasing)**

1. Сортировка паллет по убыванию ширины
2. Категоризация:
   - **Wide** (> 2W/3) → пустые секции (best-fit по высоте)
   - **Medium** (W/3..2W/3) → Address2 + крайний адрес
   - **Narrow** (≤ W/3) → добивка (до 3 паллет в секции)
3. Возврат: список placements с targetCell

**Phase 2 — Reslotting (уплотнение существующего)**

- Ищет пары частично занятых секций
- Объединяет их, освобождая целые секции
- Бюджет: `max_moves` (переменная пользователя)
- Ранжирует по эффективности: `sections_freed / cost`

## Запуск

### Локально (развёртывание)

```bash
cd wms-app
pip install -r backend/requirements.txt
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080
```

### Docker

```bash
cd wms-app
docker-compose up --build
```

Структура:
- nginx (:80) — frontend (Svelte)
- nginx (:8080) — backend (FastAPI)
- postgres (:5432) — DB (connections, snapshots)

### Конфигурация (.env)

```
DATABASE_URL=postgresql+asyncpg://wms:wms@localhost:5432/wms
LIKO_REST_URL=http://1c-server:9080/rest
LIKO_REST_LOGIN=admin
LIKO_REST_PASSWORD=password
LIKO_REST_TIMEOUT=30
```

## Offline Mode

Если подключение к 1С недоступно:
- Эндпоинты возвращают MOCK данные
- Frontend продолжает работу с локальным состоянием
- Режим сохранения snapshot'ов включён

## Запросы и Ответы

### Request: `/api/optimize` (POST)

```json
{
  "warehouse": "guid-склада",
  "pallets": [
    {"id": "pallet-1", "width": 1200, "height": 800, "depth": 1100, "weight": 500},
    {"id": "pallet-2", "width": 900, "height": 800, "depth": 1100, "weight": 450}
  ],
  "reslot": {"maxMoves": 20}
}
```

### Response: `200 OK`

```json
{
  "newPlacements": [
    {"pallet": "pallet-1", "section": "sec-5", "address": "addr-1", "rack": "rack-3", "floor": 2},
    {"pallet": "pallet-2", "section": "sec-5", "address": "addr-2", "rack": "rack-3", "floor": 2}
  ],
  "reslotMoves": [],
  "unplaced": [],
  "stats": {
    "total": 2,
    "placed": 2,
    "density": 1.0,
    "movesUsed": 0
  }
}
```

## Связи

[[Лико_WMS_Сервер]] ← вызывает через wms_client
[[wms-app-frontend]] ← визуализирует результаты
[[database]] ← PostgreSQL для конфигурации

## Статус

- ✓ Phase 1: HTTP-прокси в 1С (10 эндпоинтов)
- ✓ Phase 2A: Bin Packing optimizer
- ✓ Phase 2B: Floor optimizer
- ⚠ Phase 2C: Docker + тестирование (в разработке)
- ⏳ Phase 3: Frontend Svelte (готов, ожидает тестирования)
