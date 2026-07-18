# WMS Web Application — План реализации (v2)

> **Goal:** Веб-приложение для визуализации склада адресного хранения с оптимизацией размещения паллет.
> **Stack:** Docker, Svelte 5 + Tailwind, Python FastAPI, PostgreSQL, 1C HTTP API.
> **Source:** [specs/2026-07-17-wms-optimizer-detailed.md](../specs/2026-07-17-wms-optimizer-detailed.md)
> **v2 изменения:** добавлен алгоритм реслота (перестановка уже стоящих паллет) с настраиваемым лимитом перемещений — см. §2A.

---

## Сценарий использования (end-to-end)

1. **Пользователь открывает склад** — веб-приложение загружает топологию (`WMS_GetRacks`) + занятость (`WMS_GetOccupancy`) + пол (`WMS_GetFloor`)
2. **Запускает оптимизатор высот этажей** — для выбранного стеллажа алгоритм анализирует распределение паллет по высотам и подбирает оптимальные типоразмеры секций для каждого этажа **из существующего каталога** (120 типоразмеров `Лико_ТипоразмерыСкладскихСекций`)
3. **Смотрит отчёт** — таблица: этаж → текущая высота → средняя высота паллета → % использования → рекомендуемая высота → экономия wasted space
4. **Вручную в 1С меняет параметры этажей** (типоразмеры секций) согласно рекомендациям
5. **Запускает оптимизатор размещения** (`POST /api/optimize`) — для нового распределения высот строится план: какие новые паллеты → в какие ячейки, **плюс, если включён реслот — какие уже стоящие паллеты стоит переставить** для уплотнения
6. **Жмёт «Заполнить в 1С»** — отправляет план (новые размещения + реслот-перемещения) в `WMS_ExecutePlacements`, 1С пакетно создаёт документы `Лико_ПеремещениеПаллета2_0`
7. **Результат:** успешно перемещённые паллеты + ошибки (если какие-то не переместились) с причинами

---

## Глобальные ограничения

- **Один этаж = одна высота для всего стеллажа.** `typeSize` привязан к этажу стеллажа (ТЧ `Этажи`), не к отдельной секции. Все секции этажа имеют одинаковый типоразмер.
- **Высота секции выбирается только из существующего каталога** `Лико_ТипоразмерыСкладскихСекций` (120 типоразмеров, высота 900–2350 мм). Нельзя предложить произвольную высоту.
- **`ВысотаОтПолаФиксирована`** — параметр **каждого** этажа (не только первого). Если `Истина`:
  - `ВысотаОтПола` этого этажа заблокирована — сдвинуть нельзя
  - Типоразмер этого этажа заблокирован — менять высоту секции нельзя
  - Этажи **ниже** фиксированного можно оптимизировать, но их суммарная высота = `ВысотаОтПола` этого этажа
- **Геометрия этажа:**
  - Обычный этаж: `Типоразмер.Высота + Зазор + ВысотаБалки`
  - Последний (верхний) этаж: только `Типоразмер.Высота` (балки сверху нет)
  - Первый этаж: + `ВысотаОтПола` снизу (расстояние от пола до низа балки первого этажа)
- **Общая высота стеллажа:** `ВысотаОтПола(этаж1) + Σ(обычные этажи) + Последний.Типоразмер.Высота`
- **Стеллаж разбивается на сегменты** между фиксированными этажами. Внутри каждого сегмента можно перераспределять высоты между этажами, но суммарная высота сегмента неизменна.
- **Полезная высота для паллета** = `Габарит.Высота` (из типоразмера). Паллет должен быть ≤ габарита.
- **Зазор по ширине** = `(N + 1) × widthClearance` (из этажа стеллажа, **не** хардкод).
- Ширина секции берётся из `typeSize.width` (обычно 2700 или 2300 мм).
- **Реслот ограничен настраиваемым бюджетом перемещений** (`maxMoves`, задаётся пользователем на фронте перед запуском). Алгоритм не имеет права предложить план, который требует больше перемещений, чем задано — только берёт лучшие по эффективности кандидаты в рамках бюджета.

---

## Подготовка

- [ ] Создать `d:\project\OKIL\wms-app\` со структурой:
  ```
  wms-app/
    docker-compose.yml
    frontend/          # Svelte 5 + Tailwind
    backend/           # Python FastAPI
    nginx.conf
  ```
- [ ] Создать `frontend/` через `npm create vite@latest frontend -- --template svelte-ts`
- [ ] Создать `backend/` с `requirements.txt` (fastapi, uvicorn, httpx, psycopg2, pydantic)
- [ ] Написать `docker-compose.yml` (3 сервиса: nginx+frontend, backend, postgres)
- [ ] Написать `nginx.conf` с проксированием `/api/*` → `backend:8080`

## Задача 0: 1С — новый эндпоинт WMS_ExecutePlacements

**Файлы:**
- Modify: `1s/ERP/extensions/liko/CommonModules/Лико_WMS_Сервер/Ext/Module.bsl` — добавить функцию
- Modify: `1s/ERP/extensions/liko/HTTPServices/Liko_Rest/Ext/Module.bsl` — добавить прокси

**Назначение:** пакетное выполнение плана размещения. Принимает список `{pallet, targetCell}` и для каждого создаёт документ `Лико_ПеремещениеПаллета2_0`. Возвращает детальный результат по каждому паллету.

**Важно:** функция универсальна — ей не важно, стоял ли паллет уже где-то на складе (реслот-перемещение) или он новый (с пола). Она просто создаёт документ перемещения из текущего местоположения паллета в `targetCell`. Это значит, что доработок в 1С для поддержки реслота на уровне Задачи 0 **не требуется** — вся логика «что и куда двигать» формируется на стороне Python (см. Задачу 2A) и приходит уже готовым списком `placements`.

**BSL-функция:**
```bsl
Функция WMS_ExecutePlacements(ПараметрыPOST) Экспорт

    // НАЗНАЧЕНИЕ:
    //   Пакетное выполнение плана размещения. Для каждого placement
    //   создаёт документ Лико_ПеремещениеПаллета2_0.
    //   Универсальна для новых паллет и для реслот-перемещений уже
    //   стоящих паллет — разницы на уровне 1С нет.
    //
    // ПАРАМЕТРЫ:
    //   warehouse   — GUID склада
    //   placements  — Массив [{pallet, targetCell}, ...]

    Если НЕ ПараметрыPOST.Свойство("warehouse")
       ИЛИ НЕ ПараметрыPOST.Свойство("placements") Тогда
        Возврат HttpВернутьОшибку_WMS("MISSING_PARAM",
            "Необходимы параметры: warehouse, placements");
    КонецЕсли;

    Склад = СсылкаПоСтрокеGUID(ПараметрыPOST.warehouse, "Справочник.Склады");
    МассивPlacements = ПараметрыPOST.placements;

    МассивРезультатов = Новый Массив;
    Успешно = 0;
    Ошибок = 0;

    Для Каждого Placement Из МассивPlacements Цикл
        Результат = Новый Структура;
        Результат.Вставить("pallet", Placement.pallet);

        Попытка
            Паллет = СсылкаПоСтрокеGUID(Placement.pallet, "Справочник.Лико_Паллеты2_0");
            Ячейка = СсылкаПоСтрокеGUID(Placement.targetCell, "Справочник.СкладскиеЯчейки");

            Если НЕ ЗначениеЗаполнено(Паллет)
               ИЛИ НЕ ЗначениеЗаполнено(Ячейка) Тогда
                Результат.Вставить("ok", Ложь);
                Результат.Вставить("error", "Паллет или ячейка не найдены");
                Ошибок = Ошибок + 1;
            Иначе
                Док = Документы.Лико_ПеремещениеПаллета2_0.СоздатьПеремещение(Паллет, Ячейка);
                Результат.Вставить("ok", Истина);
                Результат.Вставить("document", Строка(Док.УникальныйИдентификатора()));
                Успешно = Успешно + 1;
            КонецЕсли;
        Исключение
            Результат.Вставить("ok", Ложь);
            Результат.Вставить("error", ОписаниеОшибки());
            Ошибок = Ошибок + 1;
        КонецПопытки;

        МассивРезультатов.Добавить(Результат);
    КонецЦикла;

    СтруктураОтвет = Новый Структура;
    СтруктураОтвет.Вставить("ok", Истина);
    СтруктураОтвет.Вставить("total", МассивPlacements.Количество());
    СтруктураОтвет.Вставить("moved", Успешно);
    СтруктураОтвет.Вставить("failed", Ошибок);
    СтруктураОтвет.Вставить("results", МассивРезультатов);

    Возврат Лико_HTTP_Сервер.Ответ(Лико_HTTP_Сервер.СтруктураВJSON(СтруктураОтвет));

КонецФункции
```

**Прокси в Liko_Rest:**
```bsl
ИначеЕсли ProcName = "WMS_ExecutePlacements" Тогда
    Результат = Лико_WMS_Сервер.WMS_ExecutePlacements(ПараметрыPOST);
```

**Контракт:**
```
→ POST { "ProcName": "WMS_ExecutePlacements", "warehouse": "guid",
         "placements": [
           {"pallet": "guid-1", "targetCell": "guid-101"},
           {"pallet": "guid-2", "targetCell": "guid-205"}
         ]}

← {
    "ok": true,
    "total": 2,
    "moved": 1,
    "failed": 1,
    "results": [
      {"pallet": "guid-1", "ok": true,  "document": "guid-doc-1"},
      {"pallet": "guid-2", "ok": false, "error": "Адрес занят паллетом P-00123"}
    ]
  }
```

---

## Задача 1: Python Backend — HTTP-прокси в 1С

**Создать:**
- `backend/app.py` — FastAPI приложение
- `backend/config.py` — настройки (URL 1С, логин/пароль из env)
- `backend/routers/warehouse.py` — эндпоинты склада
- `backend/services/wms_client.py` — HTTP-клиент к 1С Liko_Rest

**Эндпоинты (прокси):**

| Метод | Путь | → ProcName |
|---|---|---|
| POST | `/api/warehouses` | WMS_GetWarehouses |
| POST | `/api/racks` | WMS_GetRacks |
| POST | `/api/occupancy` | WMS_GetOccupancy |
| POST | `/api/floor` | WMS_GetFloor |
| POST | `/api/find-cell` | WMS_FindCell |
| POST | `/api/validate` | WMS_ValidatePlacement |
| POST | `/api/move` | WMS_MovePallet |
| POST | `/api/snapshot` | WMS_ExportSnapshot |
| POST | `/api/health` | WMS_CheckConnection |
| POST | `/api/placements/execute` | WMS_ExecutePlacements |

**Логика:**
- Принимает JSON от фронта → добавляет `ProcName` → POST в 1С → возвращает ответ
- Basic Auth заголовок формируется из env vars
- Таймаут 30 сек
- Обработка ошибок: если 1С недоступна → `{ok: false, error: "1C unavailable"}`

## Задача 2: Python Backend — Placement Optimizer + Floor Height Optimizer

**Создать:**
- `backend/services/optimizer.py` — алгоритм размещения паллет (greedy) + алгоритм реслота
- `backend/services/floor_optimizer.py` — алгоритм подбора высот этажей

### 2A. Алгоритм размещения партии (Bin Packing / Рюкзак) + реслот

**Принцип:** Это НЕ копия 1С `ПодобратьЯчейку`. 1С размещает **один** паллет за раз.
Веб-приложение размещает **всю партию сразу** — это задача упаковки (bin packing),
и **дополнительно** может предложить переставить уже стоящие паллеты для уплотнения
склада (реслот) — в рамках настраиваемого бюджета перемещений.

Ограничения те же что в 1С (габариты, зазоры, структура адресов), но цель другая —
максимизировать плотность размещения всей партии и/или всего склада.

#### Модель: секции = контейнеры, паллеты = предметы

```
Контейнер (секция):
  - Ширина: typeSize.width (2700 или 2300 мм)
  - Высота: typeSize.height (габарит)
  - Глубина: typeSize.depth
  - Грузоподъёмность: typeSize.weight (0 = безлимит)
  - 3 адреса (Address1, Address2, Address3)
  - Правила адресов:
    • Паллет > 2W/3 → занимает все 3 адреса
    • Паллет > W/3 → занимает Address2 + один крайний
    • Паллет ≤ W/3 → занимает 1 адрес
  - Зазор: (N+1) × widthClearance (из этажа стеллажа)

Предмет (паллет):
  - Ширина, Высота, Глубина, Вес
  - Может занять 1, 2 или 3 слота в секции
```

#### Часть 1 — Размещение новых паллет: Best-Fit-Decreasing (BFD) с добивкой

```python
def place_pallet_batch(sections, pallets, floor_params):
    """
    Размещает партию НОВЫХ паллет в секциях (не трогает уже стоящие).

    Стратегия:
    1. Паллеты сортируются по убыванию ширины (BFD — большие первыми)
    2. Группировка: широкие (>2W/3), средние (W/3..2W/3), узкие (≤W/3)
    3. Широкие — в пустые секции (best-fit по высоте и весу)
    4. Средние — в секции где Address2 свободен (добивка или новые)
    5. Узкие — добивают частично занятые секции до 3
    6. Финальный проход: попарное объединение (2 узких → 1 секция)
       — ВАЖНО: этот проход перетасовывает только паллеты из ЭТОЙ ЖЕ партии,
       ещё не закоммиченные в реальные адреса. Он НЕ двигает уже стоящие на
       складе паллеты — для этого см. build_reslot_plan() ниже.
    """

    # === Фаза 1: Широкие паллеты (> 2W/3) ===
    wide = [p for p in pallets if p.width > sec_width * 2 / 3]
    empty_sections = [s for s in sections if s.is_empty()]

    for pallet in sorted(wide, key=lambda p: p.width, reverse=True):
        candidates = [s for s in empty_sections
                      if can_fit_wide(s, pallet, floor_params)]
        if candidates:
            best = min(candidates, key=lambda s: s.height - pallet.height)
            place_wide(best, pallet)
            empty_sections.remove(best)

    # === Фаза 2: Средние паллеты (W/3 < ширина ≤ 2W/3) ===
    medium = [p for p in pallets if sec_width / 3 < p.width <= sec_width * 2 / 3]

    for pallet in sorted(medium, key=lambda p: p.width, reverse=True):
        candidates = find_sections_for_medium(sections, pallet, floor_params)
        if candidates:
            best = min(candidates,
                       key=lambda s: s.remaining_width - pallet.width)
            place_medium(best, pallet)

    # === Фаза 3: Узкие паллеты (≤ W/3) ===
    narrow = [p for p in pallets if p.width <= sec_width / 3]

    for pallet in sorted(narrow, key=lambda p: p.width, reverse=True):
        candidates = find_sections_for_narrow(sections, pallet, floor_params)
        candidates.sort(key=lambda s: -s.n_occupied)  # приоритет добивке
        if candidates:
            place_narrow(candidates[0], pallet)

    return build_placement_list(sections)  # [{pallet, targetAddress, rack, floor}, ...]


def can_fit_wide(section, pallet, fp):
    """Широкий паллет: секция должна быть пуста."""
    if not section.is_empty():
        return False
    return check_basic_constraints(section, pallet, fp)

def can_fit_medium(section, pallet, fp):
    """Средний паллет: Address2 свободен + Address1 или Address3 свободен."""
    if section.is_address_occupied(2):
        return False
    if section.is_address_occupied(1) and section.is_address_occupied(3):
        return False
    remaining = section.remaining_width
    clearance = (section.n_occupied + 2 + 1) * fp.widthClearance
    total = sum(o.width for o in section.occupied) + pallet.width + clearance
    return total <= section.width and check_basic_constraints(section, pallet, fp)

def can_fit_narrow(section, pallet, fp):
    """Узкий паллет: любой свободный адрес."""
    if section.n_occupied >= 3:
        return False
    clearance = (section.n_occupied + 1 + 1) * fp.widthClearance
    total = sum(o.width for o in section.occupied) + pallet.width + clearance
    return total <= section.width and check_basic_constraints(section, pallet, fp)

def check_basic_constraints(section, pallet, fp):
    """Общие ограничения (одинаковые с 1С)."""
    if pallet.height > section.height:  return False
    if pallet.depth > section.depth:    return False
    if not section.unlimitedWeight and pallet.weight > section.remaining_weight:
        return False
    if fp.maxLiftWeight > 0 and pallet.weight > fp.maxLiftWeight:
        return False
    return True
```

#### Часть 2 — Реслот: уплотнение уже стоящих паллет (НОВОЕ в v2)

**Идея:** искать частично занятые секции (1–2 паллета из 3 возможных адресов), которые
можно **объединить** так, чтобы часть секций освободилась полностью, а паллеты из них
переехали в другие секции. Каждое такое объединение стоит N перемещений (по числу
переезжающих паллет) и даёт выгоду в виде M освобождённых секций.

**Пример:**
```
До:
  Секция 1: [узкий 700мм] [пусто] [пусто]
  Секция 2: [узкий 750мм] [пусто] [пусто]
  Секция 3: [узкий 720мм] [пусто] [пусто]

После консолидации (2 перемещения — паллеты из Секции 2 и 3 → в Секцию 1):
  Секция 1: [узкий 700] [узкий 750] [узкий 720]   ← полностью занята
  Секция 2: [пусто] [пусто] [пусто]                ← освобождена
  Секция 3: [пусто] [пусто] [пусто]                ← освобождена

Цена: 2 перемещения. Выгода: 2 освобождённые секции.
```

**Ограничение бюджета:** пользователь задаёт `maxMoves` (настраиваемое число, никакого
хардкода). Алгоритм ранжирует кандидатов на консолидацию по эффективности
(`sections_freed / cost`) и берёт лучших, пока не исчерпан бюджет.

```python
def build_reslot_plan(sections, floor_params, max_moves):
    """
    Ищет возможности консолидации уже размещённых паллет.
    max_moves — задаётся пользователем на фронте (панель OptimizePanel),
    без встроенного значения по умолчанию в самом алгоритме — только
    в UI можно предложить дефолт (напр. 20) как подсказку, но пользователь
    его меняет свободно.
    """
    candidates = find_consolidation_candidates(sections, floor_params)

    # Эффективность: сколько секций освобождаем на 1 перемещение
    candidates.sort(key=lambda c: c["sections_freed"] / max(c["cost"], 1),
                     reverse=True)

    plan = []
    moves_used = 0
    freed_sections = set()
    used_pallets = set()

    for cand in candidates:
        if moves_used + cand["cost"] > max_moves:
            continue  # не влезает в оставшийся бюджет — пропускаем, ищем дальше
        if any(m["pallet"] in used_pallets for m in cand["moves"]):
            continue  # паллет уже участвует в другом перемещении этого плана

        plan.extend(cand["moves"])
        moves_used += cand["cost"]
        freed_sections.update(cand["freedSectionIds"])
        used_pallets.update(m["pallet"] for m in cand["moves"])

    return {
        "moves": plan,                       # [{pallet, fromAddress, toAddress, reason}, ...]
        "movesUsed": moves_used,
        "movesLimit": max_moves,
        "sectionsFreed": len(freed_sections),
    }


def find_consolidation_candidates(sections, floor_params):
    """
    Ищет пары (можно расширить до троек) частично занятых секций,
    которые можно объединить в одну — с учётом структуры адресов
    (узкий/средний/широкий — как в can_fit_narrow/medium/wide).
    """
    partial = [s for s in sections if 0 < s.n_occupied < 3]
    candidates = []

    for sec_a, sec_b in combinations(partial, 2):
        if not can_consolidate(sec_a, sec_b.occupied, floor_params):
            continue

        moves = [
            {
                "pallet": p.id,
                "fromAddress": p.address,
                "toAddress": pick_target_address(sec_a, p),
                "reason": "consolidation",
            }
            for p in sec_b.occupied
        ]
        candidates.append({
            "moves": moves,
            "cost": len(moves),
            "freedSectionIds": [sec_b.id],
        })

    return candidates


def can_consolidate(target_section, incoming_pallets, fp):
    """
    Проверяет, поместятся ли все паллеты incoming_pallets (из другой секции)
    в target_section — с той же логикой адресов, что can_fit_narrow/medium/wide.
    """
    remaining_addresses = 3 - target_section.n_occupied
    if len(incoming_pallets) > remaining_addresses:
        return False
    # Дальше — та же проверка по ширине/зазору/весу/высоте, что и при обычном
    # размещении (переиспользуем check_basic_constraints + адресные правила)
    ...
```

#### Сравнение с 1С

| | 1С `ПодобратьЯчейку` | Python `place_pallet_batch` + `build_reslot_plan` |
|---|---|---|
| На входе | 1 паллет | Партия паллет + (опционально) весь склад для реслота |
| Задача | Найти лучшую ячейку | Упаковать всё в N секций + уплотнить существующее |
| Метод | Приоритеты (13 правил ORDER BY) | Best-Fit-Decreasing (рюкзак) + жадная консолидация с бюджетом |
| Цель | Ближайшая подходящая | Максимальная плотность при ограниченном числе перемещений |
| Порядок | Один запрос = одно размещение | Все размещения + реслот за один проход |

#### Контракт `/api/optimize` (обновлён)

```json
→ POST /api/optimize
{
  "warehouse": "guid",
  "pallets": [...],           // новые паллеты для размещения (может быть пустой список)
  "reslot": {
    "enabled": true,          // включить консолидацию уже стоящих паллет
    "maxMoves": 20            // настраиваемый бюджет перемещений, без дефолта в алгоритме
  }
}

← {
    "newPlacements": [
      {"pallet": "guid-N1", "targetAddress": "addr-1", "rack": "guid-r1", "floor": 2}
    ],
    "reslotMoves": [
      {"pallet": "guid-X", "fromAddress": "addr-12", "toAddress": "addr-5", "reason": "consolidation"}
    ],
    "stats": {
      "newPlaced": 14,
      "movesUsed": 14,
      "movesLimit": 20,
      "sectionsFreedUp": 6,
      "densityGainPercent": 8.3
    }
  }
```

Если `reslot.enabled = false` (или блок `reslot` не передан) — `reslotMoves` пустой массив, поведение как в v1 (только новые паллеты).

### 2B. Оптимизация высот этажей — детальный алгоритм

**Источники данных для расчёта:**

| Данные | Откуда | Поле |
|---|---|---|
| Текущая высота секции этажа | `WMS_GetRacks.floors[].typeSize.height` | Габарит.Высота типоразмера |
| Зазор по высоте | `WMS_GetRacks.floors[].heightClearance` | СтрокаЭтаж.Зазор |
| Высота балки | `WMS_GetRacks.floors[].beamHeight` | СтрокаЭтаж.ВысотаБалки |
| Высота от пола | `WMS_GetRacks.floors[].heightFromFloor` | СтрокаЭтаж.ВысотаОтПола |
| Фиксирован ли первый этаж | `WMS_GetRacks.floors[].heightFromFloorFixed` | СтрокаЭтаж.ВысотаОтПолаФиксирована |
| Макс. вес подъёма на этаж | `WMS_GetRacks.floors[].maxLiftWeight` | СтрокаЭтаж.МаксимальныйВесПодъёмаНаЭтаж |
| Фактическая высота паллета | `WMS_GetOccupancy.sections[].addresses[].height` | ПараметрыПаллет.Типоразмер.Высота |
| Каталог типоразмеров | Справочник `Лико_ТипоразмерыСкладскихСекций` | 120 записей, высота 900–2350 мм |

**Геометрия этажа:**

```
Последний этаж (верх):
  Высота = только Типоразмер.Высота   ← балки сверху нет

Обычный этаж:
  Высота = Типоразмер.Высота + Зазор + ВысотаБалки

Первый этаж:
  + ВысотаОтПола снизу (расстояние от пола до низа балки)

Общая высота стеллажа:
  H = ВысотаОтПола(этаж1) + Σ[Этажᵢ.Типоразмер.Высота + Зазор + Балка] + Последний.Типоразмер.Высота
                                  ↑ все промежуточные этажи ↑              ↑ верхний (без балки) ↑
```

```
Пример — стеллаж 4 этажа, этаж 4 имеет ВысотаОтПолаФиксирована=Истина:

                    ← верх стеллажа (нет балки)
│   Паллет         │  Этаж 4: только Типоразмер.Высота (напр. 2100)
│                  │           Фикс — НЕ МЕНЯЕТСЯ
──────├──────────────  ← балка этажа 3
Балка ═══════════════
│ Зазор│Паллет      │  Этаж 3: Типоразмер.Высота + Зазор + Балка
──────├──────────────           МОЖНО менять типоразмер
Балка ═══════════════
│ Зазор│Паллет      │  Этаж 2: Типоразмер.Высота + Зазор + Балка
──────├──────────────           МОЖНО менять типоразмер
Балка ═══════════════
│ Зазор│Паллет      │  Этаж 1: Типоразмер.Высота + Зазор + Балка
──────├──────────────           МОЖНО менять типоразмер
       ↑ ВысотаОтПола (если 1-й этаж тоже фикс — не меняется)
─────── ПОЛ ─────────

Сегмент: этажи 1..3 → сумма = ВысотаОтПола этажа 4
  Было:  [1000, 1000, 1000] + 3×(100+120) = 3660 мм
  Стало: [800,  900,  1300]  + 3×(100+120) = 3660 мм  ← та же сумма!
```

**Шаг 1 — сбор фактических данных по этажу:**

```python
def collect_floor_data(rack_floors, occupancy_sections):
    """Для каждого этажа стеллажа собирает все паллеты."""
    floors = {}
    for section in occupancy_sections:
        floor_num = section.floor
        if floor_num not in floors:
            floors[floor_num] = []
        for addr in section.addresses:
            if addr.pallet:
                floors[floor_num].append({
                    "height": addr.height,
                    "width":  addr.width,
                    "weight": addr.weight,
                })
    return floors
```

**Шаг 2 — расчёт использования:**

```python
def floor_utilization(floor_params, pallets):
    """
    floor_params — из WMS_GetRacks.floors[N]
    pallets — список паллет на этом этаже
    """
    section_height = floor_params["typeSize"]["height"]
    sections_count = rack["sectionsCount"]  # например 17
    max_addresses = sections_count * 3      # 51 адрес

    if not pallets:
        return {"utilizationPercent": 0, "avgPalletHeight": 0, "wastedMm": 0}

    avg_height = sum(p["height"] for p in pallets) / len(pallets)
    total_pallet_height = sum(p["height"] for p in pallets)

    # Коэффициент использования: сумма высот паллет / (кол-во адресов × высота секции)
    utilization = total_pallet_height / (max_addresses * section_height) * 100

    # Средний неиспользуемый зазор над паллетом
    wasted_mm = section_height - avg_height

    return {
        "palletsCount": len(pallets),
        "avgPalletHeight": round(avg_height),
        "utilizationPercent": round(utilization, 1),
        "wastedMm": wasted_mm,
        "sectionHeight": section_height,
    }
```

**Шаг 3 — разбиение на сегменты и подбор высот:**

```python
def split_into_segments(rack_floors):
    """
    Разбивает этажи стеллажа на сегменты по фиксированным этажам.
    Фиксированный этаж — это граница сегмента. Этажи внутри сегмента
    можно оптимизировать, но их суммарная высота неизменна.
    """
    segments = []
    current_segment = []

    for floor in rack_floors:
        if floor.get("heightFromFloorFixed", False):
            if current_segment:
                segments.append({"floors": current_segment, "fixedBy": floor})
                current_segment = []
            segments.append({"floors": [floor], "fixed": True})
        else:
            current_segment.append(floor)

    if current_segment:
        segments.append({"floors": current_segment, "fixedBy": None})

    return segments


def optimize_segment(segment, pallets_by_floor, catalog_types, rack_width):
    """
    Оптимизирует высоты внутри одного сегмента.
    Суммарная высота сегмента сохраняется.
    """
    if segment.get("fixed"):
        return segment["floors"]

    floors = segment["floors"]
    fixed_by = segment.get("fixedBy")

    if fixed_by:
        total_budget = fixed_by["heightFromFloor"] - sum(
            f["heightFromFloor"] for f in floors[:1] if f.get("heightFromFloor")
        )
    else:
        total_budget = sum(
            f["typeSize"]["height"] + f.get("heightClearance", 0) + f.get("beamHeight", 0)
            for f in floors
        )

    total_pallets_height = 0
    for floor in floors:
        pallets = pallets_by_floor.get(floor["number"], [])
        max_h = max((p["height"] for p in pallets), default=0)
        total_pallets_height += max_h

    if total_pallets_height == 0:
        return floors

    for floor in floors:
        pallets = pallets_by_floor.get(floor["number"], [])
        if not pallets:
            continue

        max_pallet_height = max(p["height"] for p in pallets)
        share = max_pallet_height / total_pallets_height
        target_budget = total_budget * share

        clearance = floor.get("heightClearance", 0)
        beam = floor.get("beamHeight", 0)
        is_last = floor["number"] == len(floors)  # последний в стеллаже
        if is_last:
            target_height = target_budget
        else:
            target_height = target_budget - clearance - beam

        best = find_closest_catalog_type(target_height, catalog_types, rack_width)
        floor["recommended_type"] = best

    return floors


def find_closest_catalog_type(target_height, catalog_types, rack_width):
    """Ближайший типоразмер ≥ target_height (или ближайший снизу если нет сверху)."""
    candidates = [t for t in catalog_types if t["width"] == rack_width]
    candidates.sort(key=lambda t: t["height"])

    for ct in candidates:
        if ct["height"] >= target_height:
            return ct

    return candidates[-1] if candidates else None
```

**Шаг 4 — сборка отчёта:**

```python
def build_floor_report(rack, occupancy_sections, catalog_types):
    """Строит полный отчёт по этажам одного стеллажа."""
    rack_width = rack["floors"][0]["typeSize"]["width"] if rack["floors"] else 2700
    floor_data = collect_floor_data(rack["floors"], occupancy_sections)

    segments = split_into_segments(rack["floors"])

    for seg in segments:
        if not seg.get("fixed"):
            optimize_segment(seg, floor_data, catalog_types, rack_width)

    report = []
    for floor in rack["floors"]:
        floor_num = floor["number"]
        pallets = floor_data.get(floor_num, [])

        current = floor_utilization(floor, pallets)
        is_fixed = floor.get("heightFromFloorFixed", False)

        entry = {
            "floor": floor_num,
            "currentHeight": current["sectionHeight"],
            "avgPalletHeight": current["avgPalletHeight"],
            "palletsCount": current["palletsCount"],
            "utilizationPercent": current["utilizationPercent"],
            "wastedMm": current["wastedMm"],
            "fixed": is_fixed,
            "fixedReason": (
                "ВысотаОтПола фиксирована" if is_fixed else None
            ),
        }

        if not is_fixed and floor.get("recommended_type"):
            entry["recommended"] = floor["recommended_type"]

        report.append(entry)

    total_current = sum(r["utilizationPercent"] for r in report) / len(report)
    optimizable = [r for r in report if not r["fixed"] and r.get("recommended")]

    return {
        "rackId": rack["id"],
        "rackName": rack["name"],
        "rackWidth": rack_width,
        "totalFloors": len(rack["floors"]),
        "segments": len(segments),
        "fixedFloorCount": sum(1 for r in report if r["fixed"]),
        "floors": report,
        "summary": {
            "currentUtilization": round(total_current, 1),
            "optimizableFloors": len(optimizable),
        }
    }
```

**Эндпоинты:**
- POST `/api/optimize` — принимает `{warehouse, pallets: [{id, width, height, depth, weight}], reslot: {enabled, maxMoves}}` → возвращает `{newPlacements, reslotMoves, stats}`
- POST `/api/optimize/floors` — принимает `{warehouse, rackId}` → возвращает отчёт по этажам
- POST `/api/placements/execute` — принимает `{warehouse, placements: [{pallet, targetCell}]}` (общий список — новые размещения и реслот-перемещения объединяются на фронте перед отправкой) → прокси в WMS_ExecutePlacements

---

## Задача 3: Python Backend — Connection Manager

**Создать:**
- `backend/database.py` — подключение к PostgreSQL
- `backend/models.py` — SQLAlchemy модели
- `backend/routers/connections.py` — CRUD для хранения подключений

**Модель:**
```sql
CREATE TABLE connections (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    url VARCHAR(255),
    login VARCHAR(50),
    password VARCHAR(50),
    is_active BOOLEAN DEFAULT false
);
```

**Эндпоинты:**
- GET `/api/connections` — список
- POST `/api/connections` — создать
- PUT `/api/connections/:id` — обновить
- DELETE `/api/connections/:id` — удалить
- POST `/api/connections/:id/test` — проверить (через WMS_CheckConnection)

## Задача 4: Svelte Frontend — Каркас

**Установить:**
- Tailwind CSS v4 (`npx svelte-add@latest tailwindcss`)
- `@tailwindcss/typography`

**Создать:**
- `frontend/src/routes/+layout.svelte` — общий layout с навигацией
- `frontend/src/routes/+page.svelte` — главная (список складов)
- `frontend/src/routes/warehouse/[id]/+page.svelte` — страница склада
- `frontend/src/lib/api.ts` — API-клиент к backend

**Страницы:**
1. **Connections** — настройка подключения к 1С (URL/логин/пароль) + test button
2. **Warehouses** — список складов (запрос к бэкенду → 1С)
3. **Warehouse View** — визуализация одного склада

## Задача 5: Svelte Frontend — Компоненты

**Создать:**
- `frontend/src/lib/components/ConnectionForm.svelte` — форма подключения
- `frontend/src/lib/components/WarehouseSelector.svelte` — выбор склада (dropdown)
- `frontend/src/lib/components/RackView.svelte` — визуализация стеллажа
- `frontend/src/lib/components/SectionCell.svelte` — ячейка секции
- `frontend/src/lib/components/PalletBadge.svelte` — значок паллета
- `frontend/src/lib/components/FloorView.svelte` — паллеты на полу
- `frontend/src/lib/components/OptimizePanel.svelte` — панель оптимизации, включает:
  - список новых паллет для размещения
  - переключатель «Включить реслот» (`reslot.enabled`)
  - поле/слайдер «Максимум перемещений» (`reslot.maxMoves`, настраиваемое пользователем число, без скрытого дефолта в бэкенде — только подсказка в UI, например placeholder «20»)
  - кнопка «Рассчитать план»
- `frontend/src/lib/components/StatsBar.svelte` — статистика (занято/свободно)
- `frontend/src/lib/components/FloorHeightReport.svelte` — отчёт по высотам этажей (таблица + график)
- `frontend/src/lib/components/ReslotPlanView.svelte` — отдельный список реслот-перемещений в плане (паллет, откуда, куда, причина) — НОВОЕ в v2
- `frontend/src/lib/components/ExecutePlacementsButton.svelte` — кнопка «Заполнить в 1С» (отправляет объединённый список `newPlacements + reslotMoves`)

## Задача 6: Визуализация склада

**Схема отрисовки:**
- Стеллаж = вертикальный прямоугольник с этажами
- Этаж = горизонтальная полоса
- Секция = 2-3 ячейки в этаже
- Ячейка = прямоугольник:
  - **Пустая** — серый контур
  - **Занята (quantity=1)** — заливка + код паллета
  - **Зарезервирована (blocked=1)** — пунктирная заливка
- Паллет на полу — отдельная панель снизу

**Цвета:**
- Стеллажи: фиксированная палитра из 9 цветов (из `ЦветСтеллажа`)
- Ячейка занята: `#4CAF50` (зелёный)
- Ячейка заблокирована: `#FFC107` (жёлтый, пунктир)
- Ячейка свободна: `#E0E0E0` (серый)
- Ячейка участвует в реслоте (будет освобождена/занята после плана) — НОВОЕ: `#2196F3` (синий, пунктир) — визуально отличать от обычного плана размещения

**Интерактивность:**
- Ховер на ячейке — тултип с деталями паллета
- Клик на ячейке — выделение (для последующей валидации/перемещения)

## Задача 7: Drag-n-Drop и пакетное выполнение

**Пакетное выполнение (кнопка «Заполнить в 1С»):**
- Пользователь видит план размещения (результат `/api/optimize`: `newPlacements` + `reslotMoves`)
- Жмёт «Заполнить в 1С» → `POST /api/placements/execute` с объединённым списком `placements`
- Бэкенд → 1С `WMS_ExecutePlacements`
- 1С создаёт документы `Лико_ПеремещениеПаллета2_0` для каждого placement (без разницы — новое размещение это или реслот-перемещение)
- Результат: зелёные — успешно перемещённые, красные — с ошибками
- Пользователь видит: «Перемещено 16 из 18. 2 ошибки: ...»
- Обновление визуализации после выполнения

**Drag-n-Drop:**
- Drag паллета с пола в ячейку → вызов `/api/validate`
- Если валидно → подсветка зелёным → вызов `/api/move`
- Кнопка "Оптимизировать" → вызов `/api/optimize` → показать план размещения (новые + реслот)

**Каскадные перемещения для одиночного паллета («подвинь-и-поставь»):** отложено, не входит в 1 этап (см. решение по итогам обсуждения scope).

---

## Тестирование и верификация

### Тест 1: Проверка соединения
- Открыть веб-приложение → страница Connections
- Ввести URL/логин/пароль → кнопка Test
- Бэкенд вызывает `WMS_CheckConnection` → возвращает `{"ok":true}`
- ✅ Успех: зелёный статус "Connected"

### Тест 2: Загрузка топологии склада
- Выбрать склад из списка (WMS_GetWarehouses)
- Открыть Warehouse View
- Бэкенд вызывает WMS_GetRacks → стеллажи рисуются на экране
- Бэкенд вызывает WMS_GetOccupancy → ячейки заполняются (серые/зелёные/жёлтые пунктир)
- Бэкенд вызывает WMS_GetFloor → паллеты на полу внизу
- ✅ Успех: склад визуализирован, занятые/свободные/заблокированные ячейки различаются

### Тест 3: Отчёт по высотам этажей
- Выбрать стеллаж → нажать «Оптимизация высот»
- `POST /api/optimize/floors` → отчёт: таблица этажей с текущей/рекомендуемой высотой
- ✅ Успех: видна таблица: этаж → тек. высота → сред. паллет → % → рекоменд. → wasted
- ✅ Проверка: первый и последний этажи помечены как фиксированные
- ✅ Проверка: рекомендуемая высота есть в каталоге `Лико_ТипоразмерыСкладскихСекций`

### Тест 4: Оптимизация размещения новых паллет
- После изменения высот в 1С → нажать «Оптимизировать» (реслот выключен)
- `POST /api/optimize` с `reslot.enabled = false` → план размещения
- ✅ Успех: список новых паллетов с целевыми адресами, `reslotMoves` пустой

### Тест 5: Реслот с настраиваемым лимитом (НОВОЕ в v2)
- На складе создать (через `WMS_GenerateMockData` или вручную) 3+ секции с частично занятыми адресами, которые можно консолидировать
- Включить «Реслот», задать `maxMoves = 2`
- `POST /api/optimize` с `reslot: {enabled: true, maxMoves: 2}`
- ✅ Успех: `reslotMoves` содержит ≤ 2 перемещения, `stats.movesUsed ≤ 2`
- ✅ Проверка: увеличить `maxMoves` до 10 на том же складе → `sectionsFreedUp` не меньше, чем при лимите 2
- ✅ Проверка: `movesUsed` никогда не превышает `movesLimit`, кандидаты выбраны по убыванию эффективности (sections_freed/cost)

### Тест 6: Пакетное выполнение «Заполнить в 1С» (новые + реслот)
- После получения плана (с новыми размещениями и реслот-перемещениями) → нажать «Заполнить в 1С»
- `POST /api/placements/execute` с объединённым списком → `WMS_ExecutePlacements`
- ✅ Успех: зелёный тост «Перемещено 16 из 18»
- ✅ Ошибки: красные строки с причинами («Адрес занят», «Недостаточно ширины»)
- ✅ Проверка в 1С: открыть документы перемещения — проведены, включая реслот-перемещения уже стоявших паллет

### Тест 7: Проверка в 1С через обработку-тестер
- Открыть `ТестHttp1cerp` в 1С
- Выбрать склад/паллет/ячейку
- Нажать "Переместить (MovePallet)" → проверить что документ создался
- Нажать "Проверить ячейку (ValidatePlacement)" → проверить ответ

---

## Порядок: Задача 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7

Проверка после каждой: запустить `docker-compose up`, открыть `localhost`, убедиться что данные приходят из 1С.

## Открытые вопросы (перенесены из обсуждения scope)

- Каскадные перемещения для одиночного паллета («подвинь-и-поставь») — отложены за пределы 1 этапа.
- `can_consolidate()` в §2A описан на уровне сигнатуры и общей идеи (использует те же правила адресов, что `can_fit_narrow/medium/wide`) — перед реализацией стоит дописать полное тело функции по аналогии с существующими `can_fit_*`.
- Расширение `find_consolidation_candidates` с пар секций (`combinations(partial, 2)`) до троек — если двух секций недостаточно для значимого уплотнения на реальных данных склада, рассмотреть в следующей итерации (тройки увеличивают комбинаторику кандидатов).
