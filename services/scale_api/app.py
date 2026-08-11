"""Scale API — HTTP-сервис для чтения показаний весов СКУ I2121 (СКИ-12/Yaohua)."""
import logging
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from scale_reader import read_weight

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Scale API",
    description="HTTP-сервис для чтения показаний весов через M2M WiFi-модуль (TCP)",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# HTML-документация (корневая страница)
# ---------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scale API</title>
<style>
:root {
  --bg: #ffffff;
  --fg: #1a1a2e;
  --code-bg: #f4f4f5;
  --border: #e4e4e7;
  --accent: #2563eb;
  --ok: #16a34a;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #18181b; --fg: #e4e4e7; --code-bg: #27272a; --border: #3f3f46; --accent: #60a5fa; --ok: #4ade80; }
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--fg); line-height:1.6; padding:2rem; max-width:900px; margin:0 auto; }
h1 { font-size:2rem; margin-bottom:.25rem; }
h2 { font-size:1.25rem; margin:2rem 0 1rem; padding-bottom:.5rem; border-bottom:2px solid var(--accent); }
.endpoints { border-collapse:collapse; width:100%; margin:1rem 0; }
.endpoints th, .endpoints td { text-align:left; padding:.5rem .75rem; border:1px solid var(--border); }
.endpoints th { background:var(--code-bg); }
.method { font-weight:700; color:var(--accent); }
.tabs { display:flex; gap:.25rem; margin-bottom:0; flex-wrap:wrap; }
.tab { padding:.4rem 1rem; border:1px solid var(--border); border-bottom:none; border-radius:6px 6px 0 0; cursor:pointer; background:var(--code-bg); }
.tab.active { background:var(--bg); font-weight:600; border-bottom:2px solid var(--bg); margin-bottom:-1px; position:relative; z-index:1; }
.code-block { display:none; background:var(--code-bg); border:1px solid var(--border); border-radius:0 8px 8px 8px; padding:1rem; overflow-x:auto; }
.code-block.active { display:block; }
code { font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size:.875rem; white-space:pre; }
.resp { background:#ecfdf5; border:1px solid #a7f3d0; border-radius:8px; padding:1rem; margin:1.5rem 0; }
.resp pre { margin:0; color:#065f46; }
.env { background:var(--code-bg); border:1px solid var(--border); border-radius:8px; padding:1rem; margin:1rem 0; }
.env var { color:var(--accent); font-style:normal; }
</style>
</head>
<body>

<h1>⚖ Scale API</h1>
<p>HTTP-сервис чтения показаний весов <strong>СКУ I2121</strong> (индикатор СКИ-12 / Yaohua) через M2M WiFi-UART модуль.</p>

<h2>Развертывание</h2>

<div class="tabs">
  <button class="tab active" onclick="showDeploy('docker')">Docker</button>
  <button class="tab" onclick="showDeploy('bare')">Python</button>
</div>

<div id="deploy-docker" class="code-block active"><code>git clone https://github.com/maxshenderov/scale-api.git
cd scale-api
cp .env.example .env
# отредактируй SCALE_HOST в .env если нужно
docker compose up -d</code></div>

<div id="deploy-bare" class="code-block"><code>git clone https://github.com/maxshenderov/scale-api.git
cd scale-api
pip install -r requirements.txt
# Windows:
set SCALE_HOST=192.168.12.147
python app.py
# Linux/Mac:
SCALE_HOST=192.168.12.147 python app.py</code></div>

<h2>Endpoints</h2>
<table class="endpoints">
<tr><th>Метод</th><th>Путь</th><th>Назначение</th></tr>
<tr><td class="method">GET</td><td><code>/health</code></td><td>Проверка работоспособности</td></tr>
<tr><td class="method">GET</td><td><code>/api/weight</code></td><td>Текущее показание весов</td></tr>
<tr><td class="method">GET</td><td><code>/</code></td><td>Эта страница</td></tr>
</table>

<h2>GET /api/weight — примеры вызова</h2>

<div class="tabs">
  <button class="tab active" onclick="showTab('curl')">curl</button>
  <button class="tab" onclick="showTab('python')">Python</button>
  <button class="tab" onclick="showTab('bsl')">1С (BSL)</button>
  <button class="tab" onclick="showTab('js')">JavaScript</button>
  <button class="tab" onclick="showTab('ps')">PowerShell</button>
</div>

<div id="curl" class="code-block active"><code>curl http://localhost:8011/api/weight</code></div>

<div id="python" class="code-block"><code>import requests

r = requests.get("http://localhost:8011/api/weight")
data = r.json()
if data["ok"]:
    print(f"{data['value']} {data['unit']}")  # 5.0 kg
</code></div>

<div id="bsl" class="code-block"><code>Соединение = Новый HTTPСоединение("localhost", 8011);
Запрос = Новый HTTPЗапрос("/api/weight");
Ответ = Соединение.Получить(Запрос);

Если Ответ.КодСостояния = 200 Тогда
    Чтение = Новый ЧтениеJSON;
    Чтение.УстановитьСтроку(Ответ.ПолучитьТелоКакСтроку());
    Данные = ПрочитатьJSON(Чтение);

    Если Данные.ok Тогда
        Вес     = Данные.value;   // Число — 5.0
        Единицы = Данные.unit;    // "kg"
        Стабильно = Данные.stable; // Булево
        Режим   = Данные.mode;    // "n" (нетто) или "g" (брутто)
    КонецЕсли;
КонецЕсли;
</code></div>

<div id="js" class="code-block"><code>fetch("http://localhost:8011/api/weight")
  .then(r => r.json())
  .then(d => {
    if (d.ok) console.log(`${d.value} ${d.unit}`); // 5.0 kg
  });
</code></div>

<div id="ps" class="code-block"><code>$r = Invoke-RestMethod http://localhost:8011/api/weight
if ($r.ok) { "$($r.value) $($r.unit)" }  # 5.0 kg
</code></div>

<h2>Формат ответа</h2>
<div class="resp"><pre>{
  "ok": true,
  "value": 5.0,
  "unit": "kg",
  "stable": true,
  "mode": "n",
  "raw": "wn00005.0kg"
}</pre></div>

<table class="endpoints">
<tr><th>Поле</th><th>Тип</th><th>Описание</th></tr>
<tr><td><code>ok</code></td><td>bool</td><td>Успешно / ошибка</td></tr>
<tr><td><code>value</code></td><td>float|null</td><td>Вес</td></tr>
<tr><td><code>unit</code></td><td>string</td><td>Единицы (kg)</td></tr>
<tr><td><code>stable</code></td><td>bool</td><td>Вес стабилизирован</td></tr>
<tr><td><code>mode</code></td><td>string</td><td>"n" — нетто, "g" — брутто</td></tr>
<tr><td><code>raw</code></td><td>string</td><td>Сырая строка от весов</td></tr>
</table>

<h2>Переменные окружения</h2>
<div class="env">
<code><var>SCALE_HOST</var>=192.168.12.147</code> — IP M2M модуля<br>
<code><var>SCALE_PORT</var>=8899</code> — TCP-порт модуля<br>
<code><var>SERVER_PORT</var>=8011</code> — порт HTTP-сервера
</div>

<script>
function showTab(id) {
  var tabs = document.querySelectorAll('.tabs:last-of-type .tab');
  var blocks = document.querySelectorAll('.code-block[id]:not([id^="deploy-"])');
  tabs.forEach(function(t) { t.classList.remove('active'); });
  blocks.forEach(function(c) { c.classList.remove('active'); });
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}
function showDeploy(mode) {
  var tabs = document.querySelectorAll('.tabs:first-of-type .tab');
  tabs.forEach(function(t) { t.classList.remove('active'); });
  document.getElementById('deploy-docker').classList.remove('active');
  document.getElementById('deploy-bare').classList.remove('active');
  document.getElementById('deploy-' + mode).classList.add('active');
  event.target.classList.add('active');
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    """Документация с примерами вызова на разных языках."""
    return INDEX_HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/weight")
async def get_weight():
    """Прочитать одно показание с весов."""
    try:
        w = read_weight()
    except Exception as e:
        logger.error("Scale read error: %s", e)
        return {"ok": False, "value": None, "detail": str(e)}

    if w is None:
        return {"ok": False, "value": None, "detail": "no reading"}

    return {
        "ok": True,
        "value": w.value,
        "unit": w.unit,
        "stable": w.stable,
        "mode": w.mode,
        "raw": w.raw,
    }


if __name__ == "__main__":
    port = int(os.getenv("SERVER_PORT", "8011"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
