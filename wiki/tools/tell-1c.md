# tell-1c

> Claude Code скилл: отправить сообщение в 1С — в ПолеHTML или в активное окно.

## Назначение

Слэш-команда с тремя режимами:

| Команда | Действие |
|---------|----------|
| `/tell-1c <текст>` | Отправить текст в ПолеHTML обработки Лико_ГолосовойАссистент |
| `/tell-1c on` | Включить диктовку: голос из Telegram → буфер обмена → paste в активное окно 1С |
| `/tell-1c off` | Выключить диктовку |

## Как работает — текст в ПолеHTML

```
/tell-1c Привет → Write output/tell-1c-payload.json → Bash curl POST /test-text → Python → _latest → 1C poll
```

## Как работает — диктовка (on/off)

```
/tell-1c on → Monitor poll /latest (2s) → clipboard → paste.exe → активное окно 1С
/tell-1c off → TaskStop monitor
```

Использует `output/paste.exe` (скомпилированный C#) для вставки текста в активное окно, и PowerShell `Set-Clipboard` для копирования в буфер обмена.

## Файлы

- `.claude/commands/tell-1c.md` — инструкция для Claude
- `output/tell-1c-payload.json` — временный JSON с текстом

## Настройка автодоступа

В `.claude/settings.json` добавлены разрешения:
```json
"Bash(curl *test-text*)",
"Write(d:/project/OKIL/output/tell-1c-payload.json)"
```

Без них каждый вызов требует подтверждения.

## Кодировка

Кириллица отправляется через файл (Write создаёт UTF-8) + bash curl. PowerShell `Invoke-RestMethod` портит кириллицу (`????`).

## Связи

- [[telegram_voice]] — Python сервис, принимающий POST /test-text
- [[Лико_ГолосовойАссистент]] — 1С обработка, отображающая текст
