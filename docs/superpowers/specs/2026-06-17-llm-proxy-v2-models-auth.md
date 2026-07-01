# LLM Proxy v2 — Модели и Админка

> Две фичи к существующему LLM Proxy: (1) курируемый список моделей от провайдера, (2) парольная защита админки.

## Фича 1: Управление моделями

### Таблица provider_models

```sql
CREATE TABLE IF NOT EXISTS provider_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(provider_id, model_id)
);
```

### API

**`POST /api/providers/{id}/models/refresh`** — идёт к реальному провайдеру, получает список моделей, сливает с существующими (INSERT OR IGNORE по model_id, обновляет description). Возвращает количество загруженных.

**`GET /api/providers/{id}/models`** — возвращает все модели провайдера.

**`PUT /api/providers/{id}/models`** — сохраняет изменения (display_name, enabled) для массива моделей.

**`GET /v1/models`** (обновлён) — отдаёт только enabled модели с display_name: `{data: [{id: model_id, name: display_name, description}]}`. Если curated-список пуст — фолбэк на сырой список от провайдера.

### UI

Вкладка «Провайдеры»: у каждого провайдера кнопка **«Модели»** → модальное окно:

| ☑ | Название | ID модели | Описание |
|---|---|---|---|
| ☑ | DeepSeek V4 Pro | deepseek/deepseek-v4-pro | ... |
| ☐ | Claude Opus 4.8 | anthropic/claude-opus-4.8 | ... |

Кнопка **«🔄 Загрузить с провайдера»** — тянет свежий список.  
Кнопка **«Сохранить»** — применяет изменения display_name и enabled.

В форме создания подключения: поле «Модель» → `<select>` из enabled моделей выбранного провайдера (фильтруется при смене провайдера).

## Фича 2: Админский пароль

### Хранение

В settings: `admin_password_hash` — SHA-256 от пароля.

### API

**`GET /api/auth/status`** — `{password_set: true/false}`.

**`POST /api/auth/setup`** — установить пароль (только если ещё не задан). Принимает `{password: "..."}`.

**`POST /api/auth/login`** — проверить пароль. Возвращает `{ok: true/false}`.

Все POST/PUT/DELETE на `/api/*` требуют заголовок `X-Admin-Key: <password>`. GET — без проверки. `/v1/chat/completions`, `/ws`, `/health` — без проверки.

### UI

При заходе на `/ui`:
- Если пароль не задан → экран «🔐 Задайте пароль администратора» (2 поля: пароль + подтверждение)
- Если задан → форма входа (1 поле: пароль)
- После входа → пароль в `sessionStorage`, автоматически добавляется в заголовок `X-Admin-Key` ко всем API-запросам
- Кнопка «Выйти» в шапке

## Изменения в файлах

| Файл | Изменения |
|---|---|
| `db.py` | +`create_model`, +`get_models_by_provider`, +`update_models`, +`refresh_models_from_list`, +`get_password_hash`, +`set_password`, +`check_password`, +`is_password_set`, таблица provider_models |
| `app.py` | зависимость `verify_admin`, обновлён `GET /v1/models`, +`/api/providers/{id}/models/*`, +`/api/auth/*` |
| `static/index.html` | экран логина/установки пароля, кнопка «Выйти», модалка моделей в провайдерах, select модели в форме подключения |
| `static/app.js` | checkAuth, login, setup, logout, loadModels, refreshModels, saveModels, фильтрация моделей по провайдеру |

## Проверка

1. `curl http://it-programmer3:8765/api/auth/status` → `{password_set: false}`
2. Задать пароль через UI
3. Войти в UI с паролем
4. Загрузить модели для RouterAI
5. Включить/выключить несколько, переименовать
6. `curl http://it-programmer3:8765/v1/models` → только enabled с display_name
7. Создать подключение — модель выбирается из списка
