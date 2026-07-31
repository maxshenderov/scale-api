# wms_optimizer

> Python-сервис глобальной оптимизации размещения паллет на складе: CP-SAT (OR-Tools) + локальный Section Optimizer.

## Назначение

Отдельный FastAPI-микросервис, реализующий полную "Оптимизацию Engine" по ТЗ `ТЗ_WMS_Pallet_Optimizer_v4_FINAL`. Принимает снимок склада (секции, адреса, текущие паллеты) и список новых паллет к размещению, возвращает оптимальный план размещения/reslot'а.

Двухуровневая архитектура:
- **Global Optimizer** (`optimizer/global_optimizer.py`) — оркестратор всего пайплайна.
- **CP-SAT Solver** (`solver/cp_sat_model.py`) — Паллета→Секция через Google OR-Tools CP-SAT (глобальная комбинаторная оптимизация с учётом ширины/веса/reslot-лимитов).
- **Warm Start** (`solver/warm_start.py`) — First Fit Decreasing эвристика, готовит `AddHint` для CP-SAT.
- **Section Optimizer** (`optimizer/section_optimizer.py`) — Паллета→Адрес внутри уже назначенной секции (локальный scoring).
- **Potential** (`optimizer/potential.py`) — единственный источник формулы "потенциала секции", используется на всех уровнях (глобальном скоринге, локальном скоринге, валидации).

**Не путать** с уже существующим BSL-оптимизатором `ОптимальноеРазмещениеПаллет` в [[Лико_ОбщегоНазначенияСервер]] (см. также [[WMS_Optimizer_Summary]], [[WMS_Optimizer_Session_End]]) — тот работает целиком на стороне 1С без внешнего сервиса и без CP-SAT. `wms_optimizer` — независимый Python-сервис, вызываемый снаружи 1С (через снэпшот/эндпоинты [[Лико_WMS_Сервер]]), с другим алгоритмическим ядром (точное решение CP-SAT против ручной heuristic-логики).

Также отличается от [[wms-backend]] (порт 8080, Bin Packing BFD) — `wms_optimizer` не проксирует запросы в 1С и не хранит подключения/снэпшоты в PostgreSQL, это чистый stateless (кроме in-memory job-статусов) optimization engine, получающий снимок склада целиком во входном JSON.

## Endpoints

- `POST /api/optimize` — синхронный расчёт. Валидирует запрос (`validation/validator.py`), запускает `run_optimization()` в executor-потоке, возвращает `OptimizationResponse` целиком. 422 при `INVALID_DATA`, 500 при непредвиденной ошибке.
- `POST /api/optimize/async` — асинхронный расчёт (202 Accepted). Валидирует, создаёт job в in-memory `_jobs`, планирует выполнение через `BackgroundTasks`, возвращает `optimizationId`.
- `GET /api/optimization/{id}` — статус job (PENDING/RUNNING/COMPLETED/FAILED). 404 если id неизвестен.
- `GET /api/optimization/{id}/result` — результат job. 202 если ещё не завершён, 500 если FAILED, иначе `OptimizationResponse`.
- `GET /health` — `{"status":"ok"}`.

### Ключевые поля результата

- `solverStatus` (OPTIMAL/FEASIBLE/TIME_LIMIT/INFEASIBLE) — независимая ось от `placementStatus` (FULL/PARTIAL/NONE).
- `operations[]` — каждая операция несёт три независимых булевых флага: `sectionMove`, `addressMove`, `physicalMove = sectionMove OR addressMove`.
- `notPlaced[]` — причина в enum `NO_SPACE / WEIGHT_LIMIT / HEIGHT_LIMIT / DEPTH_LIMIT / LIFT_LIMIT / RESLOT_LIMIT / INVALID_DATA`.
- `affected.affectedSections` — только реально затронутые секции (для повторной проверки версий на стороне 1С перед выполнением — контракт §13.3/§15 ТЗ: 1С валидирует версии только affected-секций непосредственно перед выполнением, Python валидирует внутреннюю согласованность один раз при сборке входа).

## Архитектура (модули)

```
wms_optimizer/
├── main.py                  — FastAPI app, /health, uvicorn entrypoint (config/settings.json)
├── api/
│   ├── routes.py            — /optimize (sync/async), /optimization/{id}, /optimization/{id}/result
│   └── schemas.py           — весь контракт Pydantic (request/response/enums)
├── models/
│   ├── pallet.py, section.py, address.py — внутренние dataclass-модели
├── optimizer/
│   ├── global_optimizer.py  — оркестратор пайплайна (run_optimization)
│   ├── section_optimizer.py — Паллета→Адрес (локальный scoring)
│   ├── potential.py         — формула потенциала секции (§8), section_fits_pallet()
│   └── scoring.py           — compute_global_score / compute_address_score, читает config/weights.json
├── solver/
│   ├── cp_sat_model.py      — CPSATSolver (OR-Tools CP-SAT), Паллета→Секция
│   ├── cp_sat_aggregated.py — Агрегированная модель Y[тип,бакет] + residual passes
│   ├── hybrid_v3.py         — BFD + Chain-Swap быстрый солвер (~4с)
│   ├── hybrid_v5.py         — Aggregate CP-SAT + V3 reslot качество (~368с)
│   ├── feasibility.py       — compute_feasible_pairs(), фильтр допустимых пар
│   ├── config.py            — Константы солвера (FEASIBLE_PAIRS_THRESHOLD, etc.)
│   └── warm_start.py        — First Fit Decreasing для AddHint
├── validation/
│   └── validator.py         — validate_request(), ValidationError("INVALID_DATA", ...)
├── config/
│   ├── weights.json          — globalWeights / localWeights (никаких W1/W2/W3 — только явные имена)
│   └── settings.json         — defaultSettings, api (host/port 8010), logging
└── tests/
    └── test_acceptance.py   — 10 приёмочных тестов по §21 ТЗ
```

## Зависимости

`fastapi>=0.115`, `uvicorn[standard]>=0.32`, `pydantic>=2.9`, `ortools==9.15.6755` (версия из ТЗ `9.10.4067` недоступна на PyPI под текущий Python — обновлено на актуальную), `httpx>=0.27`.

`numpy`/`pandas` из ТЗ §17 сознательно не включены — не используются ни одним модулем (в ТЗ нет генетических алгоритмов, только CP-SAT + FFD + scoring, см. §5-§9), а верхние пины `==` (`0.111.0`/`0.29.0`/`2.7.1`) не собирались на Python 3.14 (`pydantic-core` без прекомпилированного wheel уходил в сборку из исходников и падал на `CERTIFICATE_VERIFY_FAILED` при попытке скачать Rust-тулчейн через корпоративный SSL-перехват) — заменены на диапазоны с готовыми wheel'ами.

```bash
cd services/wms_optimizer
pip install -r requirements.txt
python main.py   # порт 8010 (config/settings.json)
```

## Верификация

`pytest tests/test_acceptance.py -v` — 10/10 passed (2026-07-21). В процессе верификации найдены и исправлены два бага в `global_optimizer.py`/`section_optimizer.py`:

- **`_determine_not_placed_reason()`** (`global_optimizer.py`) проверял устаревшее состояние `virtual_section_pallets` — новые паллеты, размещённые раньше в том же цикле `for np in new_pallets`, туда не попадали, из-за чего секция выглядела свободнее, чем на самом деле, и причина отказа вычислялась неверно (`RESLOT_LIMIT` вместо `NO_SPACE`). Исправлено: занятость секции пополняется сразу по мере размещения каждой новой паллеты, до вычисления причины отказа для следующих.
- **`assign_addresses()`** (`section_optimizer.py`) не учитывал `allowReslot` вообще — существующие паллеты, зафиксированные CP-SAT на прежней секции (`allowReslot=false`), всё равно пересчитывались Section Optimizer'ом и могли переехать на другой адрес внутри той же секции (`addressMove=true` → `physicalMove=true`, хотя reslot запрещён). Исправлено в вызывающем коде (`global_optimizer.py`): при `allowReslot=false` существующие паллеты не передаются в `assign_addresses()` вообще — сохраняют текущий адрес (`KEEP`).

## Солверы

Выбор через `settings.solverType` в запросе:

| solverType | Алгоритм | Качество (S7) | Время (S7) | Ошибок |
|-----------|----------|:------------:|:----------:|:------:|
| `hybrid-v3` | BFD + Chain-Swap (быстрый) | 3215/3406 (94.4%) | **4с** | 0 |
| **`hybrid-v5`** | **Aggregate CP-SAT + V3 reslot** | **3238/3406 (95.1%)** | **368с** | **0** |
| `cp_sat` | OR-Tools CP-SAT агрегированная | 3332/3406 (97.8%) | 253с | 2 |
| `numpy` | NumPy greedy | 3218/3406 (94.5%) | 57с | 0 |

**Hybrid V5** (рекомендуется для качества):
1. `CPSATAggregatedSolver` — Y[тип, бакет] модель → дезагрегация → residual passes (exact CP-SAT, consolidation, virtual reslot)
2. Если `allowReslot=true` — chain-swap через V3 для неразмещённых паллет
3. Защита от WIDTH_OVERFLOW в финальной сборке

**Hybrid V3** (рекомендуется для скорости):
1. BFD с группировкой по типоразмерам (narrow → height → width → weight)
2. Chain-Swap до 5 итераций (depth 1/2/3), адаптивные срезы
3. Micro CP-SAT для добивки хвостов
4. Поддержка реслота и twoStageReslot

## Ограничения v1

- `_jobs` (async-режим) — обычный in-process dict, без персистентности. Не безопасно для multi-worker/multi-process деплоя, но соответствует v1 по ТЗ.
- CP-SAT objective в коде упрощён относительно полной формулы §9.1 (термы потенциала считаются только в постобработке, не участвуют в целевой функции решателя) — сделано для производительности солвера на больших складах (ТЗ §4 упоминает тест-конфигурацию 1190 секций / 3570 адресов).
- Не проверена производительность на реалистичном объёме данных — тесты покрывают только функциональные acceptance-кейсы (§21 ТЗ), не нагрузочные.

## Связи

[[Лико_WMS_Сервер]] — будущая точка вызова из 1С (снэпшот склада → HTTP → этот сервис → план → WMS_PlacePallets)
[[wms-backend]] — соседний Python WMS-сервис (HTTP-прокси в 1С + свой Bin Packing), другая задача и алгоритм
[[WMS_Optimizer_Summary]], [[WMS_Optimizer_Session_End]] — история BSL-версии оптимизатора (`ОптимальноеРазмещениеПаллет`), не путать с этим сервисом
[[СинхронизацияПодборВалидация]] — тот же архитектурный принцип единого источника правил применён здесь через `optimizer/potential.py` (одна формула потенциала для скоринга и валидации)
