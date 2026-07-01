# LLM Proxy v2 — Модели и Админка

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Курируемый список моделей (загрузка от провайдера, переименование, включение/выключение) + парольная защита админки.

**Architecture:** 3 файла: db.py (+1 таблица + auth), app.py (+API endpoints + middleware), static/ (+login + model modal). Сначала DB, потом API, потом UI.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, hashlib, Vanilla JS

## Global Constraints

- Пароль: SHA-256 хеш в settings.admin_password_hash
- `/api/*` POST/PUT/DELETE требуют `X-Admin-Key: <password>` header
- `/v1/models` отдаёт `{data: [{id, name, description}]}` только enabled модели
- Модели: UNIQUE(provider_id, model_id)
- UI: sessionStorage для пароля, модальное окно для моделей

---

### Task 1: DB — таблица provider_models + auth helpers

**Files:**
- Modify: `services/llm_proxy/db.py`

**Interfaces:**
- Produces:
  - `async def init_db()` — обновлён: +provider_models таблица
  - `async def get_models_by_provider(conn, provider_id: int) -> list[dict]`
  - `async def refresh_models(conn, provider_id: int, models: list[dict]) -> int`
  - `async def update_models(conn, provider_id: int, updates: list[dict]) -> int`
  - `async def get_enabled_models(conn, provider_id: int) -> list[dict]`
  - `async def get_password_hash(password: str) -> str` — SHA-256
  - `async def set_admin_password(conn, password: str)`
  - `async def check_admin_password(conn, password: str) -> bool`
  - `async def is_admin_password_set(conn) -> bool`

- [ ] **Step 1: Add provider_models table to init_db**

В `init_db`, после существующих CREATE TABLE, добавить:

```python
await conn.executescript("""
    CREATE TABLE IF NOT EXISTS provider_models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
        model_id TEXT NOT NULL DEFAULT '',
        display_name TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        UNIQUE(provider_id, model_id)
    );
""")
```

- [ ] **Step 2: Add model CRUD functions**

```python
async def get_models_by_provider(conn, provider_id: int) -> list[dict]:
    cursor = await conn.execute(
        "SELECT * FROM provider_models WHERE provider_id = ? ORDER BY enabled DESC, display_name",
        (provider_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def refresh_models(conn, provider_id: int, models: list[dict]) -> int:
    """Insert or update models from provider list. Returns count of models."""
    count = 0
    for m in models:
        await conn.execute(
            """INSERT INTO provider_models (provider_id, model_id, description)
               VALUES (?, ?, ?)
               ON CONFLICT(provider_id, model_id)
               DO UPDATE SET description = excluded.description""",
            (provider_id, m["id"], m.get("description", "")),
        )
        count += 1
    await conn.commit()
    return count


async def update_models(conn, provider_id: int, updates: list[dict]) -> int:
    """Update display_name and enabled for existing models."""
    count = 0
    for u in updates:
        cursor = await conn.execute(
            """UPDATE provider_models
               SET display_name = ?, enabled = ?
               WHERE provider_id = ? AND model_id = ?""",
            (u.get("display_name", ""), 1 if u.get("enabled") else 0,
             provider_id, u["model_id"]),
        )
        count += cursor.rowcount
    await conn.commit()
    return count


async def get_enabled_models(conn, provider_id: int) -> list[dict]:
    cursor = await conn.execute(
        "SELECT * FROM provider_models WHERE provider_id = ? AND enabled = 1 ORDER BY display_name",
        (provider_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]
```

- [ ] **Step 3: Add auth helpers**

```python
import hashlib

async def get_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


async def set_admin_password(conn, password: str):
    h = await get_password_hash(password)
    await conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)", (h,)
    )
    await conn.commit()


async def check_admin_password(conn, password: str) -> bool:
    cursor = await conn.execute(
        "SELECT value FROM settings WHERE key = 'admin_password_hash'"
    )
    row = await cursor.fetchone()
    if not row:
        return False
    return row["value"] == await get_password_hash(password)


async def is_admin_password_set(conn) -> bool:
    cursor = await conn.execute(
        "SELECT value FROM settings WHERE key = 'admin_password_hash' AND value != ''"
    )
    row = await cursor.fetchone()
    return row is not None
```

- [ ] **Step 4: Verify syntax and DB init**

```bash
cd d:/project/OKIL/services/llm_proxy
python -c "
import asyncio
from db import init_db, get_db, get_models_by_provider, refresh_models, update_models, get_enabled_models
from db import set_admin_password, check_admin_password, is_admin_password_set, get_password_hash

async def test():
    await init_db('data/test_models.db')
    async with get_db('data/test_models.db') as conn:
        # Create a provider
        await conn.execute('INSERT INTO providers (name, base_url, path, format) VALUES (?,?,?,?)',
                          ('Test', 'test.com', '/api', 'anthropic'))
        pid = (await (await conn.execute('SELECT last_insert_rowid()')).fetchone())[0]
        
        # Refresh models
        count = await refresh_models(conn, pid, [
            {'id': 'model-a', 'description': 'First model'},
            {'id': 'model-b', 'description': 'Second model'},
        ])
        assert count == 2, f'Expected 2, got {count}'
        
        # Get all
        all_m = await get_models_by_provider(conn, pid)
        assert len(all_m) == 2
        
        # Update
        await update_models(conn, pid, [
            {'model_id': 'model-a', 'display_name': 'Model A', 'enabled': True},
            {'model_id': 'model-b', 'display_name': 'Model B', 'enabled': False},
        ])
        
        # Get enabled
        en_m = await get_enabled_models(conn, pid)
        assert len(en_m) == 1
        assert en_m[0]['display_name'] == 'Model A'
        
        # Auth
        assert not await is_admin_password_set(conn)
        await set_admin_password(conn, 'secret123')
        assert await is_admin_password_set(conn)
        assert await check_admin_password(conn, 'secret123')
        assert not await check_admin_password(conn, 'wrong')
        
        print('ALL DB TESTS PASSED')
    
    import os; os.remove('data/test_models.db')

asyncio.run(test())
"
```

Expected: `ALL DB TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
git add services/llm_proxy/db.py
git commit -m "feat: add provider_models table and admin auth helpers"
```

---

### Task 2: API — endpoints + admin middleware

**Files:**
- Modify: `services/llm_proxy/app.py`

**Interfaces:**
- Consumes: db.py (get_models_by_provider, refresh_models, update_models, get_enabled_models, check_admin_password, is_admin_password_set, set_admin_password)
- Produces: `/api/auth/*`, `/api/providers/{id}/models/*`, updated `/v1/models`, admin verification dependency

- [ ] **Step 1: Add admin verification dependency**

В начало app.py, после импортов:

```python
from fastapi import Header

async def verify_admin(x_admin_key: str = Header(default="")):
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="Missing X-Admin-Key header")
    async with get_db(DB_PATH) as conn:
        if not await check_admin_password(conn, x_admin_key):
            raise HTTPException(status_code=403, detail="Invalid admin password")
    return True
```

- [ ] **Step 2: Add /api/auth/* endpoints**

```python
@app.get("/api/auth/status")
async def auth_status():
    async with get_db(DB_PATH) as conn:
        pw_set = await is_admin_password_set(conn)
    return {"password_set": pw_set}


@app.post("/api/auth/setup")
async def auth_setup(data: dict):
    async with get_db(DB_PATH) as conn:
        if await is_admin_password_set(conn):
            raise HTTPException(status_code=403, detail="Password already set")
        pw = data.get("password", "").strip()
        if len(pw) < 4:
            raise HTTPException(status_code=400, detail="Password too short (min 4 chars)")
        await set_admin_password(conn, pw)
    return {"ok": True}


@app.post("/api/auth/login")
async def auth_login(data: dict, x_admin_key: str = Header(default="")):
    pw = x_admin_key or data.get("password", "")
    async with get_db(DB_PATH) as conn:
        if await check_admin_password(conn, pw):
            return {"ok": True}
    raise HTTPException(status_code=403, detail="Invalid password")
```

- [ ] **Step 3: Add /api/providers/{id}/models/* endpoints**

```python
@app.get("/api/providers/{provider_id}/models")
async def api_get_models(provider_id: int):
    async with get_db(DB_PATH) as conn:
        return await get_models_by_provider(conn, provider_id)


@app.post("/api/providers/{provider_id}/models/refresh")
async def api_refresh_models(provider_id: int, _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        prov = await get_provider(conn, provider_id)
        if not prov:
            raise HTTPException(status_code=404, detail="Provider not found")
        # Fetch real models from provider
        key_info = await get_key_by_name(conn, "prod")  # use any key for this provider
        if not key_info:
            # Try list_keys
            keys = await list_keys(conn)
            key_info = next((k for k in keys if k["provider_id"] == provider_id), None)
        if not key_info:
            raise HTTPException(status_code=400, detail="No key configured for this provider")
        raw = await _fetch_models(key_info)
        models_list = raw.get("data", raw) if isinstance(raw, dict) else raw
        parsed = []
        for m in models_list:
            if isinstance(m, dict):
                parsed.append({
                    "id": m.get("id", m.get("name", "")),
                    "description": m.get("description", m.get("name", "")),
                })
        count = await refresh_models(conn, provider_id, parsed)
    return {"ok": True, "count": count}


@app.put("/api/providers/{provider_id}/models")
async def api_update_models(provider_id: int, data: list[dict], _: bool = Depends(verify_admin)):
    async with get_db(DB_PATH) as conn:
        count = await update_models(conn, provider_id, data)
    return {"ok": True, "count": count}
```

Добавить импорт: `from fastapi import Depends`

- [ ] **Step 4: Update /v1/models to use curated list**

```python
@app.get("/v1/models")
async def list_models():
    async with get_db(DB_PATH) as conn:
        override_enabled = await get_setting(conn, "override_enabled")
        override_key_id = await get_setting(conn, "override_key_id")

        if override_enabled == "1" and override_key_id:
            key_info = await _get_key_by_id(conn, int(override_key_id))
        else:
            keys = await list_keys(conn)
            key_info = keys[0] if keys else None

        if not key_info:
            raise HTTPException(status_code=503, detail="No proxy keys configured")

        provider_id = key_info.get("provider_id")
        curated = await get_enabled_models(conn, provider_id)

        if curated:
            return {
                "data": [
                    {"id": m["model_id"], "name": m["display_name"] or m["model_id"],
                     "description": m["description"]}
                    for m in curated
                ]
            }

        # Fallback: raw list from provider
        return await _fetch_models(key_info)
```

- [ ] **Step 5: Protect existing POST/PUT/DELETE endpoints with verify_admin**

Добавить `_, = Depends(verify_admin)` параметром ко всем:
- `api_create_provider` → `async def api_create_provider(data: dict, _: bool = Depends(verify_admin))`
- `api_delete_provider` → `async def api_delete_provider(provider_id: int, _: bool = Depends(verify_admin))`
- `api_create_key` → `_, = Depends(verify_admin)`
- `api_delete_key` → `_, = Depends(verify_admin)`
- `api_toggle_key` → `_, = Depends(verify_admin)`
- `api_set_settings` → `_, = Depends(verify_admin)`
- `api_clear_logs` → `_, = Depends(verify_admin)`

GET endpoints остаются без проверки.

- [ ] **Step 6: Test API**

```bash
cd d:/project/OKIL/services/llm_proxy
python -c "
import asyncio
from db import init_db
asyncio.run(init_db('data/proxy.db'))
"
# Start server
python -m uvicorn app:app --host 127.0.0.1 --port 8765 &
sleep 2

# Test auth status
curl -s http://127.0.0.1:8765/api/auth/status
# → {"password_set":false}

# Set password
curl -s -X POST http://127.0.0.1:8765/api/auth/setup -H "Content-Type: application/json" -d '{"password":"admin123"}'
# → {"ok":true}

# Login
curl -s -X POST http://127.0.0.1:8765/api/auth/login -H "Content-Type: application/json" -H "X-Admin-Key: admin123" -d '{}'
# → {"ok":true}

# Try without admin key
curl -s -X POST http://127.0.0.1:8765/api/providers -H "Content-Type: application/json" -d '{"name":"Test","base_url":"test.com"}'
# → 401

# With admin key
curl -s -X POST http://127.0.0.1:8765/api/providers -H "Content-Type: application/json" -H "X-Admin-Key: admin123" -d '{"name":"Test","base_url":"test.com"}'
# → {"id":...}

# Cleanup
PROV_ID=$(curl -s http://127.0.0.1:8765/api/providers | python -c "import sys,json; print(json.load(sys.stdin)[-1]['id'])")

# Refresh models
curl -s -X POST http://127.0.0.1:8765/api/providers/$PROV_ID/models/refresh -H "X-Admin-Key: admin123"
# → {"ok":true,"count":NN}

# Get models
curl -s http://127.0.0.1:8765/api/providers/$PROV_ID/models | python -c "import sys,json; d=json.load(sys.stdin); print(f'Models: {len(d)}')"

# Update models
curl -s -X PUT http://127.0.0.1:8765/api/providers/$PROV_ID/models -H "Content-Type: application/json" -H "X-Admin-Key: admin123" -d '[{"model_id":"model-a","display_name":"My Model","enabled":true}]'

# Test /v1/models
curl -s http://127.0.0.1:8765/v1/models | python -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d, indent=2)[:300])"

# Test admin required for delete
curl -s -X DELETE http://127.0.0.1:8765/api/providers/$PROV_ID -H "X-Admin-Key: admin123"
# → {"ok":true}

kill %1 2>/dev/null || true
echo 'DONE'
```

- [ ] **Step 7: Commit**

```bash
git add services/llm_proxy/app.py
git commit -m "feat: add admin auth middleware and model management API"
```

---

### Task 3: Web UI — логин + модели

**Files:**
- Modify: `services/llm_proxy/static/index.html`
- Modify: `services/llm_proxy/static/app.js`

- [ ] **Step 1: Add login/setup screen to index.html**

Заменить `<div class="app">` содержимое на:

```html
<div class="app">
  <!-- Auth overlay -->
  <div id="auth-screen" style="max-width:360px;margin:80px auto;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:30px;">
    <h2 id="auth-title">🔐 Вход</h2>
    <div class="form-field" style="margin-bottom:12px;">
      <label>Пароль</label>
      <input id="auth-password" type="password" style="width:100%">
    </div>
    <div class="form-field" id="auth-confirm-group" style="display:none;margin-bottom:12px;">
      <label>Подтверждение</label>
      <input id="auth-confirm" type="password" style="width:100%">
    </div>
    <button class="primary" onclick="doAuth()" style="width:100%;">Войти</button>
    <p id="auth-error" style="color:var(--danger);font-size:12px;margin-top:8px;display:none;"></p>
  </div>

  <!-- Main app (hidden until auth) -->
  <div id="main-app" style="display:none;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <h1>🔀 LLM Proxy</h1>
      <button class="ghost" onclick="logout()" style="font-size:13px;">🚪 Выйти</button>
    </div>
    <p class="subtitle">Прокси между 1С и LLM-провайдерами. Порт 8765.</p>

    <nav><!-- same as before --></nav>
    <div class="tab-content">
      <!-- Same tabs as current -->
    </div>
  </div>
</div>

<!-- Model edit modal -->
<div id="models-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);z-index:100;align-items:center;justify-content:center;">
  <div style="background:var(--card);border-radius:8px;padding:20px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 id="models-title">Модели</h3>
      <button onclick="closeModels()" style="background:none;border:none;font-size:20px;cursor:pointer;">✖</button>
    </div>
    <div style="margin-bottom:12px;">
      <button class="primary small" onclick="refreshModels()">🔄 Загрузить с провайдера</button>
      <button class="primary small" onclick="saveModels()" style="margin-left:8px;">💾 Сохранить</button>
    </div>
    <table><thead><tr><th>☑</th><th>Название</th><th>ID модели</th><th>Описание</th></tr></thead>
    <tbody id="models-table"></tbody></table>
  </div>
</div>
```

- [ ] **Step 2: Update app.js — auth logic**

Добавить в начало app.js:

```javascript
// ── Auth ───────────────────────────────────────────────────────────────────

let ADMIN_KEY = sessionStorage.getItem('llm_admin_key') || '';

function adminHeaders() {
  return ADMIN_KEY ? { 'X-Admin-Key': ADMIN_KEY } : {};
}

async function apiAuth(method, path, body) {
  const headers = { 'Content-Type': 'application/json', ...adminHeaders() };
  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (resp.status === 401 || resp.status === 403) {
    sessionStorage.removeItem('llm_admin_key');
    ADMIN_KEY = '';
    showAuth();
    throw new Error('Auth required');
  }
  if (!resp.ok) { const err = await resp.text(); throw new Error(err); }
  return resp.json();
}

async function checkAuth() {
  const status = await api('GET', '/api/auth/status');
  if (!status.password_set) {
    document.getElementById('auth-title').textContent = '🔐 Задайте пароль';
    document.getElementById('auth-confirm-group').style.display = 'block';
  } else {
    document.getElementById('auth-title').textContent = '🔐 Вход';
    document.getElementById('auth-confirm-group').style.display = 'none';
  }
  document.getElementById('auth-screen').style.display = 'block';
  document.getElementById('main-app').style.display = 'none';
  if (ADMIN_KEY) {
    // Try auto-login
    try {
      await apiAuth('POST', '/api/auth/login', {});
      showMain();
      return;
    } catch(e) { ADMIN_KEY = ''; }
  }
}

async function doAuth() {
  const pw = document.getElementById('auth-password').value;
  const errEl = document.getElementById('auth-error');
  errEl.style.display = 'none';
  
  const status = await api('GET', '/api/auth/status');
  try {
    if (!status.password_set) {
      const confirm = document.getElementById('auth-confirm').value;
      if (pw !== confirm) { errEl.textContent = 'Пароли не совпадают'; errEl.style.display = 'block'; return; }
      await api('POST', '/api/auth/setup', { password: pw });
    }
    ADMIN_KEY = pw;
    sessionStorage.setItem('llm_admin_key', pw);
    await apiAuth('POST', '/api/auth/login', {});
    showMain();
  } catch(e) {
    errEl.textContent = 'Неверный пароль';
    errEl.style.display = 'block';
  }
}

function showAuth() {
  document.getElementById('auth-screen').style.display = 'block';
  document.getElementById('main-app').style.display = 'none';
}

function showMain() {
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('main-app').style.display = 'block';
  loadConnections();
}

function logout() {
  sessionStorage.removeItem('llm_admin_key');
  ADMIN_KEY = '';
  checkAuth();
}

// Override api() to use adminHeaders for write operations
const _api = api;
api = function(method, path, body) {
  // GET requests don't need admin key
  if (method === 'GET') return _api(method, path, body);
  // POST/PUT/DELETE need admin
  return apiAuth(method, path, body);
};
```

Обновить `init` в конце:

```javascript
checkAuth();
setInterval(() => {
  if (document.getElementById('tab-logs') && document.getElementById('tab-logs').classList.contains('active')) loadLogs();
}, 5000);
```

- [ ] **Step 3: Update app.js — model management**

```javascript
// ── Models ────────────────────────────────────────────────────────────────

let currentProviderId = null;

async function openModels(providerId) {
  currentProviderId = providerId;
  document.getElementById('models-modal').style.display = 'flex';
  await loadModels(providerId);
}

function closeModels() {
  document.getElementById('models-modal').style.display = 'none';
  currentProviderId = null;
}

async function loadModels(providerId) {
  const list = await api('GET', '/api/providers/' + providerId + '/models');
  const tbody = document.getElementById('models-table');
  tbody.innerHTML = list.map(m => `
    <tr>
      <td><input type="checkbox" ${m.enabled ? 'checked' : ''} data-model-id="${esc(m.model_id)}" class="model-check"></td>
      <td><input type="text" value="${esc(m.display_name || m.model_id)}" data-model-id="${esc(m.model_id)}" class="model-name" style="width:200px;"></td>
      <td style="font-size:11px;color:var(--muted);">${esc(m.model_id)}</td>
      <td style="font-size:11px;color:var(--muted);">${esc(m.description).substring(0, 100)}</td>
    </tr>`).join('');
}

async function refreshModels() {
  if (!currentProviderId) return;
  await api('POST', '/api/providers/' + currentProviderId + '/models/refresh');
  await loadModels(currentProviderId);
}

async function saveModels() {
  if (!currentProviderId) return;
  const updates = [];
  document.querySelectorAll('#models-table tr').forEach(row => {
    const check = row.querySelector('.model-check');
    const name = row.querySelector('.model-name');
    if (check && name) {
      updates.push({
        model_id: check.dataset.modelId,
        display_name: name.value,
        enabled: check.checked,
      });
    }
  });
  await api('PUT', '/api/providers/' + currentProviderId + '/models', updates);
  closeModels();
  if (document.getElementById('tab-connections').classList.contains('active')) loadConnections();
}
```

Обновить `loadProviders()` — добавить кнопку «Модели»:

```javascript
// В строке таблицы провайдеров:
`<td><button class="small" onclick="openModels(${p.id})">Модели</button>
  <button class="small danger" onclick="delProvider(${p.id})">Удалить</button></td>`
```

Обновить форму создания подключения — поле «Модель» как select:

В `loadConnections()`: при загрузке формы, заполнить select моделями для выбранного провайдера.

Добавить обработчик смены провайдера в форме:

```javascript
document.getElementById('conn-provider').addEventListener('change', async function() {
  const pid = parseInt(this.value);
  const sel = document.getElementById('conn-model');
  sel.innerHTML = '<option value="">— автовыбор —</option>';
  if (pid) {
    const models = await api('GET', '/api/providers/' + pid + '/models');
    models.filter(m => m.enabled).forEach(m => {
      sel.innerHTML += `<option value="${esc(m.model_id)}">${esc(m.display_name || m.model_id)}</option>`;
    });
  }
});
```

Заменить `<input id="conn-model" placeholder="...">` на `<select id="conn-model" style="width:220px"><option value="">— автовыбор —</option></select>` в index.html.

- [ ] **Step 4: Rebuild Docker and test**

```bash
cd d:/project/OKIL/services/llm_proxy
docker compose down && docker compose up -d --build
sleep 3

# Test auth flow via API
curl -s http://it-programmer3:8765/api/auth/status
# Set password
curl -s -X POST http://it-programmer3:8765/api/auth/setup -H "Content-Type: application/json" -d '{"password":"admin123"}'
# Login
curl -s -X POST http://it-programmer3:8765/api/auth/login -H "Content-Type: application/json" -H "X-Admin-Key: admin123" -d '{}'

# Refresh models
PROV_ID=$(curl -s http://it-programmer3:8765/api/providers | python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'])")
curl -s -X POST http://it-programmer3:8765/api/providers/$PROV_ID/models/refresh -H "X-Admin-Key: admin123"

# Check /v1/models
curl -s http://it-programmer3:8765/v1/models | python -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',[])), 'models')"
```

- [ ] **Step 5: Commit**

```bash
git add services/llm_proxy/static/
git commit -m "feat: add admin login UI and model management UI"
```
