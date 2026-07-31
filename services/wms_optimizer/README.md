# WMS Pallet Optimizer

Python-сервис оптимального размещения паллет на складе с использованием OR-Tools CP-SAT solver.

**Порт:** 8010 | **Docker:** `wms-optimizer` | **Docs:** http://localhost:8010/docs

---

## Быстрый старт

```bash
# Запуск Docker
cd d:\project\OKIL
docker-compose up -d wms-optimizer

# Проверка
curl http://localhost:8010/health
# {"status":"ok"}

# Swagger UI
http://localhost:8010/docs
```

---

## Основные endpoints

| Метод | Path | Описание |
|-------|------|----------|
| GET | `/health` | Health check |
| POST | `/api/optimize` | Синхронная оптимизация |
| POST | `/api/optimize/async` | Асинхронный запуск |
| GET | `/api/optimization/{id}` | Статус async задачи |
| GET | `/api/optimization/{id}/result` | Результат async задачи |

---

## Пример из 1С

```bsl
// 1. Получить snapshot
ОтветSnapshot = Лико_WMS_Сервер.WMS_GetOccupancy(ПараметрыSnapshot);
Occupancy = ПрочитатьJSON(...);

// 2. Получить паллеты с пола
МассивПаллет = /* запрос паллет где Секция = ПустаяСсылка */;

// 3. Запрос к оптимизатору
HTTPСоединение = Новый HTTPСоединение("localhost", 8010);
HTTPЗапрос = Новый HTTPЗапрос("/api/optimize");
HTTPЗапрос.УстановитьТелоИзСтроки(СтруктураВJSON(ЗапросОптимизации));
HTTPОтвет = HTTPСоединение.ВызватьHTTPМетод("POST", HTTPЗапрос);

Результат = ПрочитатьJSON(...);

// 4. Выполнить план
ОтветВыполнения = Лико_WMS_Сервер.WMS_PlacePallets(ПараметрыВыполнения);
```

Полные примеры: [`output/WMS_TestOptimizer.bsl`](../../output/WMS_TestOptimizer.bsl)

---

## Документация

- **[API_DOCS.md](API_DOCS.md)** — полная API документация (endpoints, схемы, примеры curl и 1С)
- **[../output/README_WMS_Optimizer_Docker.md](../../output/README_WMS_Optimizer_Docker.md)** — Docker deployment guide
- **Swagger UI:** http://localhost:8010/docs
- **ReDoc:** http://localhost:8010/redoc

---

## Структура проекта

```
wms_optimizer/
├── main.py                 # FastAPI приложение
├── Dockerfile             # Docker образ
├── requirements.txt       # Python зависимости
├── API_DOCS.md           # Полная API документация
├── PLACEMENT_RULES.md    # Правила размещения (narrow aisle, габариты)
├── config/
│   ├── settings.json     # Настройки сервера
│   └── weights.json      # Веса GlobalScore
├── api/
│   ├── routes.py         # REST endpoints (sync/async)
│   └── schemas.py        # Pydantic модели
├── optimizer/
│   ├── global_optimizer.py    # Главный пайплайн (роутинг exact/aggregated)
│   ├── potential.py           # Расчёт потенциала секции
│   ├── scoring.py             # GlobalScore (метрика результата)
│   ├── section_optimizer.py   # Выбор адреса (правило 1С)
│   └── timeout_runner.py      # Wall-clock таймаут обёртка
├── solver/
│   ├── cp_sat_model.py        # OR-Tools CP-SAT точная модель (warm-start режим)
│   ├── cp_sat_aggregated.py   # Агрегированная модель Y[type,bucket] + residual passes
│   ├── hybrid_v3.py           # BFD + Chain-Swap быстрый солвер (~4с)
│   ├── hybrid_v5.py           # Aggregate CP-SAT + V3 reslot качество (~368с)
│   ├── warm_start.py          # FFD эвристика
│   ├── feasibility.py         # Фильтр допустимых пар
│   └── config.py              # Константы солвера
├── models/               # Доменные модели (Pallet, Section, Address)
├── validation/           # Валидация запросов
├── tests/
│   ├── test_s7_vs_standard.py  # Регрессионный тест холодного старта
│   ├── test_acceptance.py      # Приёмочные smoke-тесты
│   └── example/               # Тестовые фикстуры (JSON)
└── static/              # HTML UI (rules viewer)
```

---

## Управление Docker

```bash
# Логи
docker-compose logs -f wms-optimizer

# Остановка
docker-compose stop wms-optimizer

# Перезапуск
docker-compose restart wms-optimizer

# Пересборка после изменений
docker-compose build wms-optimizer
docker-compose up -d wms-optimizer

# Удаление
docker-compose down wms-optimizer
```

---

## Алгоритм

### Hybrid V5 (макс. качество, `solverType: "hybrid-v5"`)

1. **Агрегированная CP-SAT модель** (`solver/cp_sat_aggregated.py`)
2. **Дезагрегация** — жадное распределение паллет по секциям внутри бакетов
3. **Residual passes** — exact CP-SAT → consolidation → virtual reslot
4. **Реслот через V3** — если `allowReslot=true`, неразмещённые паллеты проходят chain-swap
5. **Защита от WIDTH_OVERFLOW** — финальная проверка (N+1)*gap при сборке ответа

### Основной пайплайн (холодный старт, 0 existing)

1. **Агрегированная CP-SAT модель** (`solver/cp_sat_aggregated.py`)
   - Переменные `Y[type, bucket]` — целочисленный счётчик паллет данного типа в данном бакете секций
   - Сокращает комбинаторику с O(N_паллет × N_секций) до O(T_типов × B_бакетов)
   - На реальном складе (3406 паллет, 1490 секций): **2.3M** → **19K** переменных (~100× меньше)
   - Автоматически включается когда `feasible_pairs > 300k`, иначе точная модель

2. **Дезагрегация** — жадное распределение конкретных паллет по конкретным секциям внутри каждого bucket (best-fit по занятости)

3. **Residual passes** — для паллет которые основная модель не разместила:
   - **Exact CP-SAT** — неагрегированная модель X[pallet, section] на остатке
   - **Consolidation** — освобождение недозаполненных секций (≥1/3 free width) путём консолидации их одиночных жильцов
   - **Virtual reslot** — совместная переоптимизация leftover + уже (виртуально) размещённых new_pallets в near-miss секциях

4. **Section Optimizer** — выбор адреса внутри уже назначенной секции по правилу 1С:
   - Большая паллета (width > 2/3 секции) → Адрес2 (центр)
   - Иначе: Адрес1 → Адрес3 → Адрес2 (первый свободный)

### Warm-start режим (existing > 0, allowReslot=true)

1. **FFD Warm Start** — быстрая эвристика First-Fit Decreasing для начального решения
2. **CP-SAT Solver** (`solver/cp_sat_model.py`) — точная модель с reslot-переменными, warm start от FFD
3. **Section Optimizer** — как выше

**Ограничения размещения:**
- Высота/глубина/вес паллеты ≤ соответствующих пределов секции
- Ширина с зазорами: `SUM(pallet.width) + (N+1)*gap_width ≤ section.width`
- Узкопроходные стеллажи: `pallet.width ≤ max_widthPallet` (если задано)
- Вес секции: `SUM(pallet.weight) ≤ max_weight` (если не unlimited)

Веса целевой функции (GlobalScore для метрики в ответе) настраиваются в [`config/weights.json`](config/weights.json).

---

## Performance

| Солвер | solverType | Паллет | Секций | Время | Размещено | Ошибок |
|--------|-----------|--------|--------|------:|:---------:|:------:|
| **Hybrid V3** | `hybrid-v3` | 3406 | 1490 | **4с** | 3215 (94.4%) | 0 |
| **Hybrid V5** | `hybrid-v5` | 3406 | 1490 | 368с | **3238 (95.1%)** | 0 |
| CP-SAT agg | `cp_sat` | 3406 | 1490 | 253с | 3332 (97.8%) | 2 |
| NumPy | `numpy` | 3406 | 1490 | 57с | 3218 (94.5%) | 0 |
| Ручной эталон | — | 3406 | 1490 | — | 3242 (95.2%) | 0 |

**Cold start (0 existing, 3406 new, 1490 sections)** — S7 тестовые данные.

**Рекомендации:**
- Нужна скорость → `hybrid-v3` (4с, 94.4%)
- Нужно качество → `hybrid-v5` (368с, 95.1%, 0 ошибок)
- Нужно максимальное качество → `cp_sat` (253с, 97.8%)
- < 500 паллет → синхронный режим `/api/optimize`
- > 500 паллет → асинхронный режим `/api/optimize/async`

---

## Конфигурация

### config/settings.json

```json
{
  "api": {
    "host": "0.0.0.0",
    "port": 8010,
    "workers": 1
  },
  "solver": {
    "default_time_limit_seconds": 120,
    "max_time_limit_seconds": 600
  }
}
```

### config/weights.json

```json
{
  "globalWeights": {
    "placedPalletWeight": 100000,
    "sectionMovePenalty": 1000,
    "addressMovePenalty": 100,
    "potentialLossPenalty": 50,
    "spaceLossPenalty": 10,
    "sectionUsagePenalty": 5
  },
  "localWeights": {
    "widthResidualPenalty": 10,
    "potentialLossPenalty": 50,
    "futurePotentialReward": 20
  }
}
```

**Примечание:** `localWeights` (для scoring адреса) больше не используются — выбор адреса реализован детерминированным правилом 1С (см. раздел Алгоритм выше).

---

## Технологии

- **Python 3.11**
- **FastAPI** — REST API framework
- **OR-Tools 9.15** — Google Optimization Tools (CP-SAT solver)
- **Pydantic** — валидация данных
- **Uvicorn** — ASGI server
- **Docker** — контейнеризация

---

## Связанные файлы

- [`../../output/WMS_TestOptimizer.bsl`](../../output/WMS_TestOptimizer.bsl) — тестовый BSL-скрипт для 1С
- [`../../output/README_WMS_Optimizer_Docker.md`](../../output/README_WMS_Optimizer_Docker.md) — Docker deployment
- [`../../docker-compose.yml`](../../docker-compose.yml) — Docker Compose конфигурация
- [`../../wiki/services/WMSOptimizer.md`](../../wiki/services/WMSOptimizer.md) — документация в вики (TODO)

---

**Версия:** 1.0.0  
**Дата:** 2026-07-21  
**Автор:** Max Shenderov
