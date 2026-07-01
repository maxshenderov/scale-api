# /tell-1c — отправить сообщение в 1С

Слэш-команда `/tell-1c <текст|on|off>`.

## Режимы

### `on` — Включить диктовку в активное окно

Запустить фоновый процесс (Bash `run_in_background`). Каждые 2 секунды опрашивает `/latest`. Новое голосовое → буфер обмена → `paste.exe` в активное окно 1С.

ВАЖНО: использовать `run_in_background` bash, НЕ Monitor — Monitor шлёт notifications на каждое сообщение и тратит токены.

**Алгоритм:**
1. Проверить что сервис доступен: `curl -s http://localhost:8004/latest` (если нет — ошибка: «Сервис не запущен»)
2. Убедиться что `output/paste.exe` существует (если нет — ошибка)
3. Запустить Bash с `run_in_background: true, timeout: 600000`:

```bash
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
last=""
while true; do
  result=$(curl -s "http://localhost:8004/latest?since=$since")
  text=$(echo "$result" | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('text',''))")
  if [ -n "$text" ] && [ "$text" != "$last" ]; then
    echo "$text" > /d/project/OKIL/output/dictation/dict_clip.txt
    powershell.exe -NoProfile -Command "Get-Content 'd:\project\OKIL\output\dictation\dict_clip.txt' -Encoding UTF8 | Set-Clipboard"
    sleep 0.5
    /d/project/OKIL/output/paste.exe
    last="$text"
  fi
  sleep 2
done
```

4. Сохранить `task_id` из ответа (он в формате `bg...`)
5. Подтвердить: «Диктовка включена — говори в Telegram, текст будет вставляться в активное окно ✅»
6. Добавить: «⚠️ Фоновый процесс живёт 10 минут. Закроешь Claude — остановится.
   Чтобы работало независимо: двойной клик на `output/dictation/Диктовка.bat` — запустит приложение в трее Windows.
   Либо `bash d:/project/OKIL/output/dictation/dictation_loop.sh` в Git Bash-окне.»

### `off` — Выключить диктовку

Вызвать `TaskStop` с сохранённым `task_id` (bg-задача). Подтвердить: «Диктовка остановлена ✅»

Если task_id не найден (новая сессия) — просто подтвердить что активных задач диктовки нет.

### Любой другой текст — отправить в ПолеHTML

1. Записать текст в `output/tell-1c-payload.json` в формате `{"text":"<текст>"}`
2. Вызвать Bash:
   ```
   curl -s --data-binary @d:/project/OKIL/output/tell-1c-payload.json -H "Content-Type: application/json; charset=utf-8" http://localhost:8004/test-text
   ```
3. Если `{"ok":true}` → «Сообщение отправлено в 1С ✅»
4. Если ошибка соединения → «Сервис не запущен. Запусти docker контейнер telegram-voice»

ВАЖНО: НЕ использовать PowerShell для отправки — он портит кириллицу. Только через файл + Bash curl.

## Самостоятельный запуск (без Claude)

**Основной способ:** двойной клик на `output/dictation/Диктовка.bat` → иконка в трее Windows.

| Файл | Зачем |
|---|---|
| `Диктовка.bat` | Лаунчер в трей (двойной клик) |
| `Диктовка.ps1` | PowerShell-приложение с трей-иконкой |
| `dictation.pyw` | Python/tkinter оконное приложение |
| `dictation.html` | Веб-интерфейс (PWA) |
| `dictation_loop.sh` | Bash-скрипт для Git Bash окна |

В трее: **левый клик** по иконке — вкл/выкл. **Правый клик** — меню (Включить/Выключить/Выход).
При новой диктовке — всплывает balloon с текстом, текст в буфере обмена.

## Примеры

- `/tell-1c Привет` → текст в ПолеHTML
- `/tell-1c on` → запуск фоновой диктовки (без уведомлений)
- `/tell-1c off` → остановка диктовки
