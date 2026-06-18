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
      <td><button class="small danger" onclick="delProvider(${p.id})">Удалить</button></td>
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

// ── Init ────────────────────────────────────────────────────────────────

loadConnections();
setInterval(() => {
  if (document.getElementById('tab-logs').classList.contains('active')) loadLogs();
}, 5000);
