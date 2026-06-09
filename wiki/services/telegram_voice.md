# telegram_voice

> Telegram Voice → STT → HTTP → 1C ПолеHTML. Фаза 2: универсальный AI-ассистент с инструментами.

## Назначение

Сервис принимает голосовые сообщения из Telegram, транскрибирует через faster-whisper, принимает контекст из 1С (инструменты + system_prompt), вызывает LLM через OpenRouter с function calling, и возвращает ответ в 1С через HTTP polling.

## Архитектура

```
Telegram → aiogram (long polling) → faster-whisper (STT)
                                         ↓
1C Форма → POST /context ←────────── SessionManager
                                         ↓
                                      LLM Client (OpenRouter) → function calling → ответ
                                         ↓
1C Форма ← GET /latest (poll 1s) ←─── WSManager._latest
```

- Telegram Bot API через long polling (`getUpdates`) — не нужен публичный URL
- faster-whisper модель `small` (2GB) — локально, русский язык
- 1C подключается через HTTP (poll `/latest`), WebSocket остался для совместимости

## Endpoints

| Endpoint | Метод | Назначение |
|---|---|---|
| `/context` | POST | Принять контекст из 1С: session_id, chat_id, tools, system_prompt |
| `/latest` | GET | Получить последний ответ AI (опрашивается 1С каждую секунду) |
| `/test-text` | POST | Вставить тестовый текст (без Telegram) — для отладки в РФ |
| `/display` | GET | HTML-страница для ПолеHTML с автоопросом /latest |
| `/ws` | WebSocket | WebSocket для 1С (старая совместимость) |

### POST /context

```json
{
  "session_id": "uuid",
  "chat_id": 123456,
  "form_name": "Форма",
  "assistant_type": "Склад",
  "form_context": {},
  "tools": [{...}],
  "system_prompt": "Ты ассистент склада..."
}
```

### POST /test-text

```json
{"text": "Привет"}
→ {"ok": true, "text": "Привет", "type": "ai_response"}
```

Используется Claude-скиллом `/tell-1c` для отправки сообщений в 1С.

## LLM конфигурация (`.env`)

```bash
LLM_BASE_URL=https://aichat-okil-sato.kartochka.tech/api/v1/chat/completions
LLM_API_KEY=sk-5ad444531ffa461a92ea0ddcd4f92a02
LLM_MODEL=anthropic/claude-haiku-4.5
LLM_MAX_TOKENS=2000
LLM_TEMPERATURE=0.1
```

Смена модели = одна строка в `.env` + `docker restart telegram-voice`.

## Запуск

```bash
cd services/telegram_voice
docker build -t telegram-voice .
docker run -d -p 8004:8004 \
  --env-file .env \
  -v whisper_models:/app/whisper_models \
  --name telegram-voice telegram-voice
```

## Зависимости

- aiogram 3.x — Telegram Bot
- faster-whisper (ctranslate2) — STT
- FastAPI + uvicorn — HTTP/WebSocket
- pydantic — модели запросов
- httpx — LLM HTTP клиент
- ffmpeg — ogg → wav конвертация (в Docker)

## Файлы

| Файл | Назначение |
|---|---|
| `app.py` | FastAPI entry point: все endpoints, CORS, lifespan |
| `bot.py` | aiogram bot: long polling, скачивание .ogg, вызов STT → LLM |
| `stt.py` | faster-whisper: .ogg → текст |
| `ws_manager.py` | Управление WebSocket + хранение `_latest` для /latest |
| `session.py` | Управление сессиями: chat_id → контекст, tools, system_prompt |
| `llm.py` | OpenRouter клиент с function calling |
| `Dockerfile` | Контейнер с ffmpeg и whisper |
| `.env` | Токены и LLM-конфигурация |

## Связи

- [[Лико_ГолосовойАссистент]] — 1С обработка-потребитель
- [[specs/2026-05-29-telegram-voice-1c-spec]] — спецификация Фазы 1
- [[tell-1c]] — Claude-скилл отправки сообщений в 1С
