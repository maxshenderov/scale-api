# LLM Proxy — Умный прокси для 1С → LLM

> Docker-сервис с веб-интерфейсом. Принимает OpenAI-формат от 1С, переводит в Anthropic Messages для RouterAI (DeepSeek без Alibaba), управляет ключами, логирует.
> Решает проблему: RouterAI на `/api/v1/chat/completions` иногда маршрутизирует в дорогой Alibaba ($0.15/1K), а на `/api` (Anthropic Messages) — всегда в дешёвый DeepSeek ($0.001/1K).

## Архитектура

```
1С Обработка (OpenAI формат)
        │
        ▼  HTTP POST /v1/chat/completions
        │  Authorization: Bearer <имя_ключа>
        │  WebSocket /ws?key=<имя_ключа> (будущее)
        │
┌───────┴──────────────────────────────────────────┐
│  LLM Proxy (Docker, FastAPI, port 8765)          │
│                                                   │
│  /ui              Веб-интерфейс (Vanilla JS)      │
│  /v1/chat/completions   Прокси запросов           │
│  /v1/models             Список моделей             │
│  /ws                    WebSocket (будущее)        │
│  /health                Статус                     │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  SQLite (aiosqlite)                          │ │
│  │  ├── providers       Внешние провайдеры      │ │
│  │  ├── proxy_keys      Ключи доступа к прокси  │ │
│  │  ├── settings        Настройки/override      │ │
│  │  └── request_log     Логи запросов           │ │
│  └─────────────────────────────────────────────┘ │
└──────┬───────────────────────────────────────────┘
       │
       ▼
┌──────────┐  ┌──────────┐  ┌────────────┐
│ RouterAI │  │ Omniun   │  │ Любой      │
│ /api     │  │ /openai  │  │ провайдер  │
│(Messages)│  │(pass-thru)│  │            │
└──────────┘  └──────────┘  └────────────┘
```

## Сущности (SQLite)

### providers
| Поле | Тип | Описание |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT UNIQUE | "RouterAI", "Omniun" |
| base_url | TEXT | "routerai.ru" (без https://) |
| path | TEXT | "/api" или "/v1/chat/completions" |
| format | TEXT | "anthropic" или "openai" |
| port | INTEGER | 443 (по умолчанию) |

### proxy_keys
| Поле | Тип | Описание |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT UNIQUE | "prod" — то что 1С шлёт в Authorization |
| provider_id | INTEGER FK → providers.id | |
| real_key | TEXT | Настоящий API-ключ провайдера |
| default_model | TEXT | "deepseek/deepseek-v4-pro" |
| enabled | INTEGER | 1/0 |

### settings
| Поле | Тип | Описание |
|---|---|---|
| key | TEXT PK | "override_enabled", "override_key_id" |
| value | TEXT | "1" / имя ключа |

### request_log
| Поле | Тип | Описание |
|---|---|---|
| id | INTEGER PK | |
| timestamp | TEXT | ISO8601 |
| key_name | TEXT | Имя proxy-ключа |
| provider | TEXT | Имя провайдера |
| model | TEXT | Использованная модель |
| tokens_in | INTEGER | |
| tokens_out | INTEGER | |
| duration_ms | INTEGER | |
| error | TEXT | Текст ошибки или NULL |

## API Endpoints

### POST /v1/chat/completions
Принимает OpenAI Chat Completions формат от 1С.

**Заголовки:** `Authorization: Bearer <имя_ключа>`  
**Тело:** `{model, messages, temperature?, max_tokens?, tools?}`

**Логика:**
1. Найти proxy_key по имени из Bearer
2. Если override_enabled → использовать override_key_id
3. Найти provider
4. Если provider.format == "anthropic" → перевести OpenAI → Anthropic Messages
5. Отправить запрос, перевести ответ обратно → OpenAI
6. Записать в request_log

### GET /v1/models
Возвращает список моделей в формате `{data: [{id, name, description}]}`.  
Берёт из provider'а (запрос к `/api/v1/models` или `/v1/models`).

### GET /health
`{"status": "ok"}`

### WebSocket /ws?key=<имя_ключа> (будущее)
JSON-сообщения: `{type: "chat", id, body: {model, messages, tools}}` → `{type: "response", id, body}`

## Веб-интерфейс (/ui)

Одностраничное приложение (Vanilla JS), 4 вкладки:

### Ключи (главная)
- Таблица: имя, провайдер, модель по умолчанию, enabled (галочка)
- Кнопки: Создать, Удалить
- Форма создания: имя, выбор провайдера (select), реальный API-ключ (password), модель по умолчанию

### Провайдеры
- Таблица: имя, URL, формат (OpenAI/Anthropic)
- Кнопки: Добавить, Удалить
- Форма: имя, base_url, path, формат (select)

### Настройки
- Галочка «Форсировать провайдера/модель»
- Выпадающий список: выбор proxy_key для форсирования
- При включении — все запросы идут через выбранного провайдера с его моделью

### Логи
- Таблица последних 200 запросов
- Колонки: время, ключ, провайдер, модель, токены in/out, длительность, ошибка
- Автообновление каждые 5 секунд
- Кнопка «Очистить логи»

## Трансляция форматов

### OpenAI → Anthropic Messages
- `messages[role=system]` → поле верхнего уровня `system`
- `messages[role=user]` → `{role: "user", content: "текст"}`
- `messages[role=assistant]` (текст) → `{role: "assistant", content: "текст"}`
- `messages[role=assistant]` (tool_calls) → `{role: "assistant", content: [{type: "text", ...}, {type: "tool_use", id, name, input}]}`
- `messages[role=tool]` → `{role: "user", content: [{type: "tool_result", tool_use_id, content}]}`
- `tools[{function: {name, description, parameters}}]` → `[{name, description, input_schema: parameters}]`

### Anthropic → OpenAI
- `content[{type: "text"}]` → `choices[0].message.content`
- `content[{type: "tool_use"}]` → `choices[0].message.tool_calls[{id, function: {name, arguments: json}}]`
- `stop_reason: "tool_use"` → `finish_reason: "tool_calls"`
- `stop_reason: "end_turn"` → `finish_reason: "stop"`
- `usage: {input_tokens, output_tokens}` → `usage: {prompt_tokens, completion_tokens}`

## Что меняется в 1С

Только `Провайдеры()` — добавляется провайдер «Proxy»:

```bsl
Proxy = Новый Структура;
Proxy.Вставить("КлючAPI",    "prod");              // имя ключа в прокси
Proxy.Вставить("URLСервера", "<ip-сервера>");      // адрес Docker-хоста
Proxy.Вставить("Путь",       "/v1/chat/completions");
Proxy.Вставить("МаксТокенов", 32768);
Провайдеры.Вставить("Proxy", Proxy);
```

И `ПровайдерПоУмолчанию()`:
```bsl
Структура.Вставить("Имя",     "Proxy");
```

Всё остальное в обработке не меняется.

## Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8765
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
```

**Зависимости:** `fastapi`, `uvicorn`, `httpx`, `aiosqlite` (без ORM).

**docker-compose.yml** — монтирует `./data` для персистентности SQLite.

## Структура файлов

```
services/llm_proxy/
  app.py              # FastAPI сервер, все endpoint'ы
  db.py               # SQLite (aiosqlite) — инициализация, CRUD
  translator.py        # OpenAI ↔ Anthropic трансляция
  static/
    index.html         # SPA веб-интерфейс
    app.js             # Логика UI
  requirements.txt     # fastapi, uvicorn, httpx, aiosqlite
  Dockerfile
  docker-compose.yml
```

## Порядок реализации

1. `db.py` — инициализация БД, функции CRUD
2. `translator.py` — OpenAI ↔ Anthropic
3. `app.py` — эндпоинты: `/health`, `/v1/models`, `/v1/chat/completions`
4. `static/` — веб-интерфейс (ключи, провайдеры, настройки, логи)
5. Dockerfile + docker-compose.yml
6. Изменения в 1С (`Провайдеры` + `ПровайдерПоУмолчанию`)

## Проверка

1. `curl localhost:8765/health` → `{"status": "ok"}`
2. Через веб-интерфейс добавить провайдера RouterAI и ключ "test"
3. `curl -X POST localhost:8765/v1/chat/completions -H "Authorization: Bearer test" -H "Content-Type: application/json" -d '{"model":"deepseek/deepseek-v4-pro","messages":[{"role":"user","content":"1+1"}]}'` → ответ с choices
4. Включить override → запрос должен пойти через форсированного провайдера
5. Открыть `/ui` → увидеть запрос в логах
