// LLM Proxy — Web UI (Vanilla JS SPA)

const API = '';

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

// ── Tab navigation ──────────────────────────────────────────────────────

document.querySelectorAll('nav button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'logs') loadLogs();
    if (btn.dataset.tab === 'connections') loadConnections();
    if (btn.dataset.tab === 'providers') loadProviders();
    if (btn.dataset.tab === 'settings') loadSettings();
  });
});

// ── API helpers ─────────────────────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (!resp.ok) { const err = await resp.text(); throw new Error(err); }
  return resp.json();
}

// ── Providers ───────────────────────────────────────────────────────────

async function loadProviders() {
  const list = await api('GET', '/api/providers');
  const tbody = document.getElementById('providers-table');
  tbody.innerHTML = list.map(p => `
    <tr>
      <td><strong>${esc(p.name)}</strong></td>
      <td>${esc(p.base_url)}:${p.port}${esc(p.path)}</td>
      <td><span class="badge ${p.format === 'anthropic' ? 'badge-info' : 'badge-ok'}">${esc(p.format)}</span></td>
      <td><button class="small" onclick="openModels(${p.id})">Модели</button>
        <button class="small danger" onclick="delProvider(${p.id})">Удалить</button></td>
    </tr>`).join('');
  // Update connection form's provider select
  const sel = document.getElementById('conn-provider');
  if (sel) {
    sel.innerHTML = list.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  }
}

async function createProvider() {
  const name = document.getElementById('prov-name').value.trim();
  const url = document.getElementById('prov-url').value.trim();
  if (!name || !url) return alert('Имя и Хост обязательны');
  const data = {
    name, base_url: url,
    path: document.getElementById('prov-path').value.trim() || '/v1/chat/completions',
    format: document.getElementById('prov-format').value,
    port: parseInt(document.getElementById('prov-port').value) || 443,
  };
  await api('POST', '/api/providers', data);
  document.getElementById('prov-name').value = '';
  document.getElementById('prov-url').value = '';
  loadProviders();
}

async function delProvider(id) {
  if (!confirm('Удалить провайдера и все его подключения?')) return;
  await api('DELETE', '/api/providers/' + id);
  loadProviders();
  loadConnections();
}

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

// ── Connections (бывшие Keys) ───────────────────────────────────────────

async function loadConnections() {
  const list = await api('GET', '/api/keys');
  const tbody = document.getElementById('connections-table');
  const empty = document.getElementById('connections-empty');
  if (list.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
  } else {
    empty.style.display = 'none';
    tbody.innerHTML = list.map(k => `
      <tr>
        <td><code style="background:#f1f5f9;padding:2px 6px;border-radius:3px;font-size:13px;">${esc(k.name)}</code></td>
        <td>${esc(k.provider_name)}</td>
        <td style="color:var(--muted);font-size:12px;">${esc(k.default_model) || '—'}</td>
        <td>${k.enabled ? '<span class="badge badge-ok">активно</span>' : '<span class="badge badge-err">отключено</span>'}</td>
        <td>
          <button class="small ghost" onclick="toggleConnection(${k.id}, ${k.enabled})">${k.enabled ? 'отключить' : 'включить'}</button>
          <button class="small danger" onclick="deleteConnection(${k.id})">Удалить</button>
        </td>
      </tr>`).join('');
  }
  // Update override selector
  const sel = document.getElementById('override-key');
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = '<option value="">— не выбрано —</option>' +
      list.map(k => `<option value="${k.id}">${esc(k.name)} (${esc(k.provider_name)} → ${esc(k.default_model)})</option>`).join('');
    sel.value = cur;
  }
}

async function createConnection() {
  const name = document.getElementById('conn-name').value.trim();
  const realKey = document.getElementById('conn-key').value.trim();
  const providerId = parseInt(document.getElementById('conn-provider').value);
  if (!name || !realKey || !providerId) return alert('Имя, Провайдер и API-ключ обязательны');
  const data = {
    name,
    provider_id: providerId,
    real_key: realKey,
    default_model: document.getElementById('conn-model').value.trim(),
  };
  await api('POST', '/api/keys', data);
  document.getElementById('conn-name').value = '';
  document.getElementById('conn-key').value = '';
  loadConnections();
}

async function deleteConnection(id) {
  if (!confirm('Удалить подключение? 1С больше не сможет его использовать.')) return;
  await api('DELETE', '/api/keys/' + id);
  loadConnections();
}

async function toggleConnection(id, current) {
  await api('POST', '/api/keys/' + id + '/toggle', { enabled: !current });
  loadConnections();
}

// ── Settings ────────────────────────────────────────────────────────────

async function loadSettings() {
  const s = await api('GET', '/api/settings');
  const toggle = document.getElementById('override-toggle');
  if (s.override_enabled === '1') toggle.classList.add('on');
  else toggle.classList.remove('on');
  if (s.override_key_id) {
    document.getElementById('override-key').value = s.override_key_id;
  }
}

async function toggleOverride() {
  const toggle = document.getElementById('override-toggle');
  const isOn = toggle.classList.contains('on');
  const newState = isOn ? '0' : '1';
  await api('POST', '/api/settings', { override_enabled: newState });
  loadSettings();
}

async function saveOverrideKey() {
  const val = document.getElementById('override-key').value;
  await api('POST', '/api/settings', { override_key_id: val });
}

// ── Logs ────────────────────────────────────────────────────────────────

async function loadLogs() {
  const list = await api('GET', '/api/logs?limit=200');
  const tbody = document.getElementById('logs-table');
  const counter = document.getElementById('log-count');
  if (counter) counter.textContent = 'Записей: ' + list.length;
  tbody.innerHTML = list.map(l => `
    <tr>
      <td style="font-size:11px;color:var(--muted);">${esc(l.timestamp)}</td>
      <td><code>${esc(l.key_name)}</code></td>
      <td>${esc(l.provider)}</td>
      <td style="font-size:12px;">${esc(l.model)}</td>
      <td>${l.tokens_in}</td>
      <td>${l.tokens_out}</td>
      <td>${l.duration_ms}</td>
      <td>${l.error ? '<span class="badge badge-err">' + esc(l.error.substring(0, 60)) + '</span>' : '✅'}</td>
    </tr>`).join('');
}

async function clearLogs() {
  if (!confirm('Очистить все логи?')) return;
  await api('DELETE', '/api/logs');
  loadLogs();
}

// ── Utils ───────────────────────────────────────────────────────────────

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── API override: use adminHeaders for write operations ─────────────────

const _api = api;
api = function(method, path, body) {
  if (method === 'GET') return _api(method, path, body);
  return apiAuth(method, path, body);
};

// ── Init ────────────────────────────────────────────────────────────────

checkAuth();
setInterval(() => {
  if (document.getElementById('tab-logs') && document.getElementById('tab-logs').classList.contains('active')) loadLogs();
}, 5000);

// ── Provider change -> populate model select ────────────────────────────

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
