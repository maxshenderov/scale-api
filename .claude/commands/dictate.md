# /dictate — Telegram Voice → Claude Code

Получить текст из Telegram бота @Okil1c_bot (голос или текст).
Работает через `telegram_voice` сервис (localhost:8004).

## Режимы

### `/dictate` — разовый запрос

Взять текущее время (ISO), запросить `/latest?since=$NOW`, показать текст.
Если пусто — сообщить «Новых сообщений нет».

```bash
curl -s "http://localhost:8004/latest?since=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

**Важно:** `since` = ТЕКУЩЕЕ время. Это гарантирует, что старые сообщения не покажутся.

### `/dictate on` — непрерывный мониторинг

1. Запомнить `START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)`
2. Запустить Monitor (persistent=true) с командой:

```bash
since="START_TIME"
last=""
while true; do
  result=$(curl -s "http://localhost:8004/latest?since=$since")
  text=$(echo "$result" | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('text',''))")
  if [ -n "$text" ] && [ "$text" != "$last" ]; then
    echo "$text" > PROJECT/output/dict_clip.txt
    powershell.exe -NoProfile -Command "Get-Content 'PROJECT\output\dict_clip.txt' -Encoding UTF8 | Set-Clipboard"
    PROJECT/output/paste.exe
    echo "ГОЛОС: $text"
    echo "ГОЛОС: $text"
    last="$text"
  fi
  sleep 2
done
```
PROJECT — заменить на реальный путь к корню проекта: `/d/project/OKIL` в bash и `d:\project\OKIL` в PowerShell.

Подставить реальный `START_TIME` в команду.

3. Сохранить `task_id` из результата Monitor

### `/dictate off` — остановить мониторинг

Вызвать `TaskStop` для `task_id` сохранённого при `/dictate on`.

## Endpoint

- `GET /latest?since=ISO_TIME` — возвращает `{"text":"...", "type":"transcription"}` только если сообщение новее `since`. Иначе `{"text":"","type":"empty"}`.

## Troubleshooting

| Проблема | Решение |
|----------|---------|
| Connection refused | `docker start telegram-voice` |
| `text: ""` | Отправь голосовое/текст в @Okil1c_bot |
| Нет контейнера | `docker ps --filter name=telegram-voice` |
