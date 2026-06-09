# /tell-1c — отправить сообщение в 1С

Слэш-команда `/tell-1c <текст|on|off>`.

## Режимы

### `on` — Включить диктовку в активное окно

Запустить непрерывный мониторинг `/latest`. Каждое новое голосовое сообщение из Telegram → буфер обмена → paste в активное окно 1С.

**Алгоритм:**
1. Проверить что сервис доступен: `curl -s http://localhost:8004/latest` (если нет — ошибка)
2. Запустить Monitor (persistent=true) с bash-командой:

```bash
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
last=""
while true; do
  result=$(curl -s "http://localhost:8004/latest?since=$since")
  text=$(echo "$result" | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('text',''))")
  if [ -n "$text" ] && [ "$text" != "$last" ]; then
    echo "$text" > /d/project/OKIL/output/dict_clip.txt
    powershell.exe -NoProfile -Command "Get-Content 'd:\project\OKIL\output\dict_clip.txt' -Encoding UTF8 | Set-Clipboard"
    sleep 0.5
    /d/project/OKIL/output/paste.exe
    echo "ГОЛОС: $text"
    last="$text"
  fi
  sleep 2
done
```

3. Сохранить `task_id` из ответа Monitor
4. Подтвердить: «Диктовка включена — говори в Telegram, текст будет вставляться в активное окно ✅»

### `off` — Выключить диктовку

Вызвать `TaskStop` с сохранённым `task_id`. Подтвердить: «Диктовка остановлена ✅»

### Любой другой текст — отправить в ПолеHTML

1. Записать текст в `output/tell-1c-payload.json` в формате `{"text":"<текст>"}`
2. Вызвать Bash:
   ```
   curl -s --data-binary @d:/project/OKIL/output/tell-1c-payload.json -H "Content-Type: application/json; charset=utf-8" http://localhost:8004/test-text
   ```
3. Если `{"ok":true}` → «Сообщение отправлено в 1С ✅»
4. Если ошибка соединения → «Сервис не запущен. Запусти docker контейнер telegram-voice»

ВАЖНО: НЕ использовать PowerShell для отправки — он портит кириллицу. Только через файл + Bash curl.

## Примеры

- `/tell-1c Привет` → текст в ПолеHTML
- `/tell-1c on` → запуск диктовки в активное окно
- `/tell-1c off` → остановка диктовки
