# CLAUDE.md — OKIL Project

This file provides guidance to Claude Code when working with this repository.
For 1C architecture, BSL patterns, data model — see [AGENTS.md](AGENTS.md).

---

## Project Overview

ERP system for **Лико** — a label printing company (self-adhesive, shrink sleeve, FMCG packaging). Two halves:

1. **1C:Enterprise ERP** (`1s/ERP/Conf/`) — XML dump in EDT format. Base: `УправлениеПредприятием`. All custom objects use prefix `Лико_`.
2. **Python FastAPI backend** (`services/`) — AI/LLM integration: document parsing, RAG search, batch indexing, parameter generation for orders.
3. **Wiki** (`wiki/`) — живая документация проекта. Claude читает перед любым кодом.

---

## ⚡ ПРАВИЛО №1 — ПЕРЕД ЛЮБЫМ КОДОМ

**Перед написанием BSL или Python кода — ВСЕГДА:**

1. Прочитай `wiki/index.md`
2. Найди похожие объекты в вики (аналогичный документ, модуль, сервис)
3. Прочитай их страницы
4. Пиши в том же стиле, с теми же модулями и соглашениями

Не придумывай имена и паттерны — смотри как сделано у нас в вики.
После написания кода — сразу сделай INGEST нового объекта в вики.

### ⚡ Сабагенты (Agent/Explore/Plan) — жёсткое правило

- **Максимум 2 сабагента** на запрос (не 3+)
- **Prompt без дублирования:** только конкретная задача + список файлов. НЕ копируй CLAUDE.md/AGENTS.md/wiki в prompt сабагента
- **Wiki в сабагентах:** только если задача напрямую требует знаний из вики. Не пиши "прочитай wiki/index.md" в prompt сабагента без крайней необходимости
- **После ответа сабагента:** извлекай только нужное, не цитируй файлы целиком

---

## ЧЕТЫРЕ ОПЕРАЦИИ С ВИКИ

### CODE — написать код

```
wiki/index.md → найти похожее → прочитать страницы → писать код → ingest
```

Готовый BSL клади в `output/`. Готовый Python — в нужный `services/<service>/`.

### INGEST — добавить/обновить объект в вики

Когда я говорю *"добавь в вики [объект]"* или *"я изменил [файл]"*:

1. Прочитай BSL/Python файл
2. Определи тип: BSL-модуль / документ / расширение / Python-сервис
3. Создай или **обнови** страницу в нужной папке `wiki/`
4. Обнови `wiki/index.md` — одна строка описания
5. Добавь вики-ссылки `[[НазваниеСтраницы]]` на связанные страницы
6. Добавь запись в `wiki/log.md`

### QUERY — ответить на вопрос

1. Прочитай `wiki/index.md`
2. Найди 2–4 релевантные страницы
3. Прочитай их → ответь со ссылками
4. Хороший ответ = кандидат на новую страницу вики → предложи сохранить

### LINT — проверить вики

1. Прочитай все страницы
2. Найди противоречия, устаревшее, страницы без ссылок
3. Найди объекты которые упоминаются но страниц нет
4. Предложи список что добавить следующим

---

## Структура директорий

| Директория | Назначение |
|---|---|
| `1s/ERP/Conf/` | 1С EDT исходники — только чтение |
| `1s/ERP/obrab/` | Обработки и расширения — только чтение |
| `services/` | Python микросервисы |
| `wiki/` | Вики — Claude читает и пишет |
| `output/` | Готовый BSL-код от Claude |
| `wiki/extensions/` | Страницы расширений проекта |
| `wiki/liko/` | Страницы Лико_-объектов |
| `wiki/ai/` | AI обработки и сервисы |
| `wiki/modules/` | Общие BSL-модули |
| `wiki/documents/` | Документы 1С |
| `wiki/services/` | Python сервисы |
| `wiki/patterns/` | Паттерны кода |

---

## Python Services Architecture

Five independently-runnable FastAPI microservices:

```
1C (ЗаказКлиента + PDF) → Parser (PyMuPDF/EasyOCR) → Docling → Hybrid Search → RAG LLM
                                                           ↕
                                                     Qdrant (port 6334)
                                                    Batch Indexer fills it
```

| Service | Port | Purpose |
|---|---|---|
| `parser/` | 8002 | PyMuPDF (PDF) + EasyOCR (JPG/PNG) — local document extraction |
| `docling/` | 8001 | IBM Docling structured PDF/DOCX; Vision LLM (Gemini) for image OCR |
| `hybrid_search/` | 8002 | Qdrant BM25 (sparse) + Dense embeddings hybrid search |
| `rag_llm/` | 8003 | RAG: TOP-3 similar orders → prompt → LLM → parameters + confidence |
| `batch_indexer/` | N/A | Fetches orders from 1C API, parses via Docling, loads into Qdrant |

**Note:** `parser/` и `hybrid_search/` оба на 8002 — не запускать одновременно.

```bash
cd services/<service_name>
pip install -r ../requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port <port>
```

Core deps: `fastapi`, `uvicorn`, `pydantic`, `qdrant-client`, `sentence-transformers`,
`rank-bm25`, `httpx`, `PyMuPDF`, `easyocr`, `docling`.

---

## Qdrant Setup

| Instance | Port | Назначение |
|---|---|---|
| Port 6333 | code indexing (Roo Code) |
| Port 6334 | orders (`orders_phase1` collection) |

```bash
python qdrant_setup.py --host localhost --port 6334 [--recreate]
```

Dual named vectors: `phase1_text_dense` (COSINE, 384d) и `phase1_text_bm25` (DOT, sparse).

---

## LLM API

- `rag_llm/` → `aichat-okil-sato.kartochka.tech` (OpenAI-compatible proxy)
- `docling/` Vision LLM → `routerai.ru`
- API keys hardcoded в service files — нужно перенести в env vars.
- SSL: `PYTHONHTTPSVERIFY=0` (корпоративная сеть)

---

## 1C — коротко (полное описание в AGENTS.md)

- Все кастомные объекты: префикс `Лико_`
- Центральный документ: `ЗаказКлиента` с формой `Лико_ФормаДокумента`
- Главный модуль: `Лико_ОбщегоНазначенияСервер` (~10900 строк)
- Тег авторства: `//+Лико m.shenderov 11.09.2017`
- Обработка ошибок: `Попытка...Исключение` + `Лико_ДополнительноКлиентСервер.СообщитьДляКлиента()`
- Перед написанием BSL — читай `wiki/liko/` и `wiki/extensions/`

---

## Форматы страниц вики

### Лико_-объект (`wiki/liko/НазваниеОбъекта.md`)
```
# Лико_НазваниеОбъекта

> Одна строка — суть объекта.

## Что делает
## Ключевые процедуры
- `ИмяПроцедуры()` — что делает
## Связь с ЗаказКлиента
## Используемые модули
## Связи [[...]]
```

### Расширение (`wiki/extensions/НазваниеРасш.md`)
```
# НазваниеРасширения

> Одна строка — что расширяет и зачем.

## Что расширяет
## Новые объекты ⚡
## Переопределения ⚡
## Новые реквизиты ⚡
## Подписки на события
## Связи с Лико_-объектами
## Связи [[...]]
```

### Python сервис (`wiki/services/НазваниеСервиса.md`)
```
# НазваниеСервиса

> Одна строка — суть сервиса.

## Назначение
## Endpoints
- `POST /endpoint` — что делает, параметры, ответ
## Зависимости
## Связи [[...]]
```

### Паттерн (`wiki/patterns/НазваниеПаттерна.md`)
```
# НазваниеПаттерна

> Когда использовать.

## Пример кода
## Антипаттерн
```

---

## Ключевая документация

- [AGENTS.md](AGENTS.md) — 1С архитектура, BSL паттерны, структура данных, AI интеграция
- [MD/RouterAI Chat 4 мая 2026.md](MD/RouterAI%20Chat%204%20мая%202026.md) — спецификация AI интеграции
- [plans/АРХИТЕКТУРА_PYTHON_ПРОЕКТА.md](plans/АРХИТЕКТУРА_PYTHON_ПРОЕКТА.md) — архитектура Python
- `plans/` — планы по всем фазам разработки
- `wiki/` — живая документация (важнее plans/)

---

## Testing

Только ручные/интеграционные тесты.
`services/parser/test_parser.py` — тест парсера.
pytest не настроен.
