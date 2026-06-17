// LLM Proxy — Web UI (Vanilla JS SPA)

const API = '';

// ── Tab navigation ──────────────────────────────────────────────────────

document.querySelectorAll('nav button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'logs') loadLogs();
    if (btn.dataset.tab === 'keys') loadKeys();
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
      <td>${esc(p.name)}</td>
      <td>${esc(p.base_url)}</td>
      <td>${esc(p.path)}</td>
      <td><span class="badge badge-ok">${esc(p.format)}</span></td>
      <td><button class="small danger" onclick="delProvider(${p.id})">Удалить</button></td>
    </tr>`).join('');
  // Also update key form's provider select
  const sel = document.getElementById('key-provider');
  sel.innerHTML = list.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
}

async function createProvider() {
  const data = {
    name: document.getElementById('prov-name').value,
    base_url: document.getElementById('prov-url').value,
    path: document.getElementById('prov-path').value,
    format: document.getElementById('prov-format').value,
    port: parseInt(document.getElementById('prov-port').value) || 443,
  };
  await api('POST', '/api/providers', data);
  document.getElementById('prov-name').value = '';
  document.getElementById('prov-url').value = '';
  loadProviders();
}

async function delProvider(id) {
  if (!confirm('Удалить провайдера и все его ключи?')) return;
  await api('DELETE', '/api/providers/' + id);
  loadProviders();
  loadKeys();
}

// ── Keys ────────────────────────────────────────────────────────────────

async function loadKeys() {
  const list = await api('GET', '/api/keys');
  const tbody = document.getElementById('keys-table');
  tbody.innerHTML = list.map(k => `
    <tr>
      <td><strong>${esc(k.name)}</strong></td>
      <td>${esc(k.provider_name)}</td>
      <td>${esc(k.default_model)}</td>
      <td><div class="toggle ${k.enabled ? 'on' : ''}" onclick="toggleKey(${k.id}, ${k.enabled})"></div></td>
      <td><button class="small danger" onclick="delKey(${k.id})">Удалить</button></td>
    </tr>`).join('');
  // Update override key selector
  const sel = document.getElementById('override-key');
  const cur = sel.value;
  sel.innerHTML = '<option value="">— не выбрано —</option>' +
    list.map(k => `<option value="${k.id}">${esc(k.name)} (${esc(k.provider_name)})</option>`).join('');
  sel.value = cur;
}

async function createKey() {
  const data = {
    name: document.getElementById('key-name').value,
    provider_id: parseInt(document.getElementById('key-provider').value),
    real_key: document.getElementById('key-real').value,
    default_model: document.getElementById('key-model').value,
  };
  await api('POST', '/api/keys', data);
  document.getElementById('key-name').value = '';
  document.getElementById('key-real').value = '';
  loadKeys();
}

async function delKey(id) {
  if (!confirm('Удалить ключ?')) return;
  await api('DELETE', '/api/keys/' + id);
  loadKeys();
}

async function toggleKey(id, current) {
  await api('POST', '/api/keys/' + id + '/toggle', { enabled: !current });
  loadKeys();
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
  tbody.innerHTML = list.map(l => `
    <tr>
      <td>${esc(l.timestamp)}</td>
      <td>${esc(l.key_name)}</td>
      <td>${esc(l.provider)}</td>
      <td>${esc(l.model)}</td>
      <td>${l.tokens_in}</td>
      <td>${l.tokens_out}</td>
      <td>${l.duration_ms}</td>
      <td>${l.error ? '<span class="badge badge-err">' + esc(l.error.substring(0, 80)) + '</span>' : ''}</td>
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

// ── Init ────────────────────────────────────────────────────────────────

loadKeys();
setInterval(() => {
  if (document.getElementById('tab-logs').classList.contains('active')) loadLogs();
}, 5000);
