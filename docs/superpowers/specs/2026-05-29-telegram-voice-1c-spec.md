# Spec: Telegram Voice → 1C HTML Поле (Фаза 1)

> Дата: 2026-05-29 | Статус: approved

## Контекст

Конечная цель — AI-ассистент по складу в 1С. Пользователь диктует вопрос/задачу в Telegram, LLM обрабатывает с инструментами, результат в 1С.

**Фаза 1:** голосовое сообщение из Telegram → текст в HTML-поле на форме 1С.

## Фазы проекта

| Фаза | Содержание |
|---|---|
| Фаза 1 | Голос в Telegram → текст в HTML-поле 1С (этот документ) |
| Фаза 2 | Подключение LLM + инструментов (загрузка секций, алгоритм подбора и т.д.) |
| Фаза 3 | Генерация документации/оценки задач с голоса |

## Архитектура Фазы 1

```
┌──────────┐     голосовое (.ogg)     ┌──────────────┐
│ Telegram │ ──────────────────────→  │ tg.telegram.org │
│ (телефон) │                         └──────┬───────┘
└──────────┘                                 │
                                    getUpdates (long polling)
                                             │
                                    ┌────────▼───────┐
                                    │  Python Service │  ← inside OKIL network
                                    │                 │
                                    │  aiogram        │  ← Telegram Bot
                                    │  faster-whisper │  ← STT (local)
                                    │  fastapi + ws   │  ← WebSocket Server
                                    └────────┬───────┘
                                             │ ws://
                                    ┌────────▼───────┐
                                    │  1C Form        │
                                    │  ВебСокетКлиент │  ← native 1C WebSocket
                                    │  ПолеHTML       │  ← displays text
                                    └────────────────┘
```

### Ключевые решения

- **Telegram Bot API доступ:** Long polling (`getUpdates`). Бот сам ходит в интернет. Никаких публичных URL, белых IP, пробросов портов не нужно.
- **STT:** `faster-whisper` local. Модель `small` (2GB VRAM / ~4GB RAM). Бесплатно, русский язык хорошо.
- **1С WebSocket:** Нативный объект `ВебСокетКлиент` (не своя WebSocket подсистема на `int.okil.ru`). 1С подключается как клиент к Python.
- **WebSocket библиотека Python:** `websockets` (асинхронная, совместима с aiogram).

### Протокол WebSocket (JSON)

```json
// Python → 1C
{
  "type": "transcription",
  "text": "почему 1с предложила эту ячейку для размещения",
  "timestamp": "2026-05-29T15:30:00",
  "user": "Макс"
}
```

Одно сообщение = одно голосовое. Пока без сессий и накопления.

## Структура кода

### Python сервис

```
services/telegram_voice/
├── app.py              # FastAPI + WebSocket server, entry point
├── bot.py              # aiogram bot: получает голосовые, запускает STT, пишет в очередь
├── stt.py              # faster-whisper: транскрипция .ogg → текст
├── ws_manager.py       # Управление WebSocket подключениями от 1С
└── requirements.txt    # aiogram, faster-whisper, fastapi, uvicorn, websockets
```

### 1С объекты

```
1s/ERP/extensions/liko/
├── Обработки/
│   └── Лико_ГолосовойАссистент/     # Новая обработка
│       └── Forms/
│           └── Форма/Ext/
│               ├── Form.xml          # Форма с ПолеHTML + ВебСокетКлиент
│               └── Form/Module.bsl   # Логика формы
```

## Поток данных (шаг за шагом)

1. Пользователь записывает голосовое в Telegram → боту
2. `aiogram` получает `Message` с `Voice` → скачивает `.ogg` во временную папку
3. `faster-whisper` транскрибирует `.ogg` → текст
4. `ws_manager` отправляет текст всем подключенным 1С-клиентам через WebSocket
5. 1С `ВебСокетКлиент` получает JSON → `ОбработкаСообщения()` → обновляет HTML-поле
6. HTML-поле показывает текст

## Окружение и зависимости

- **Python 3.10+** с установленным `faster-whisper` (тянет `ctranslate2`, `tokenizers`)
- **CUDA** или **CPU-only** режим (параметр `device="cpu"` или `"cuda"`)
- **Telegram Bot Token** — через `@BotFather`
- **1С тонкий клиент** — `ВебСокетКлиент` доступен в тонком клиенте

## Запуск

```bash
cd services/telegram_voice
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=12345:abcde
python app.py
```

```bsl
// 1С: открыть обработку Лико_ГолосовойАссистент
// форма автоматически подключается к WebSocket
```

## Готовность к Фазе 2 (LLM)

WebSocket протокол уже поддерживает поле `type`. Для Фазы 2 добавляется:
- Клиент → Сервер: `{"type": "question", "text": "...", "context": "warehouse"}`
- Сервер → Клиент: `{"type": "ai_response", "text": "...", "analysis": {...}}`

Структура не меняется, только добавляются новые `type`.

## Ограничения

- Одно WebSocket подключение = одна открытая форма 1С
- Если форма закрыта — текст не доставляется (пока без очереди/retry)
- faster-whisper `small` на CPU: ~20-30 сек на транскрипцию 10-секундного сообщения
- На GPU (CUDA): ~2-3 сек
