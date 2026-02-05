#!/usr/bin/env python3
"""Demo web UI for saas_infra instance management.

NOT FOR PRODUCTION. Run with:
    set -a && source .env && set +a
    uv run python -m saas_infra.demo
"""

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .provisioner import Provisioner, ProvisioningConfig
from .railway_client import RailwayAPIError, RailwayClient


class CreateRequest(BaseModel):
    """Request body for creating an instance."""

    name: str
    repo: str | None = None


_client: RailwayClient | None = None


def _get_client() -> RailwayClient:
    global _client
    if _client is None:
        _client = RailwayClient.from_env()
    return _client


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _get_client()
    yield


app = FastAPI(title="Luthien Proxy — Instance Manager (Demo)", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
async def _index():
    return PAGE_HTML


@app.get("/api/instances")
async def _list_instances():
    try:
        client = _get_client()
        instances = client.list_luthien_instances()
        return {
            "instances": [
                {
                    "name": inst.name,
                    "project_id": inst.project_id,
                    "status": inst.status.value,
                    "url": inst.url,
                    "railway_url": f"https://railway.com/project/{inst.project_id}",
                    "created_at": inst.created_at.isoformat() if inst.created_at else None,
                    "deletion_scheduled_at": (
                        inst.deletion_scheduled_at.isoformat() if inst.deletion_scheduled_at else None
                    ),
                    "services": {
                        name: {"id": svc.id, "name": svc.name, "status": svc.status.value, "url": svc.url}
                        for name, svc in inst.services.items()
                    },
                }
                for inst in instances
            ]
        }
    except RailwayAPIError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/instances")
async def _create_instance(req: CreateRequest):
    try:
        client = _get_client()
        config = ProvisioningConfig()
        if req.repo:
            config.repo = req.repo

        provisioner = Provisioner(client, config)

        # Run in thread pool since CLI calls block
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, provisioner.create_instance, req.name)

        if not result.success or result.instance is None:
            return JSONResponse({"error": result.error or "Unknown error"}, status_code=400)

        inst = result.instance
        return {
            "name": inst.name,
            "project_id": inst.project_id,
            "url": inst.url,
            "railway_url": f"https://railway.com/project/{inst.project_id}",
            "status": inst.status.value,
            "proxy_api_key": result.proxy_api_key,
            "admin_api_key": result.admin_api_key,
        }
    except RailwayAPIError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/instances/{name}")
async def _get_instance(name: str):
    try:
        client = _get_client()
        instance = client.get_instance(name)
        if not instance:
            return JSONResponse({"error": f"Instance '{name}' not found"}, status_code=404)

        return {
            "name": instance.name,
            "project_id": instance.project_id,
            "status": instance.status.value,
            "url": instance.url,
            "created_at": instance.created_at.isoformat() if instance.created_at else None,
            "deletion_scheduled_at": (
                instance.deletion_scheduled_at.isoformat() if instance.deletion_scheduled_at else None
            ),
            "services": {
                name: {"id": svc.id, "name": svc.name, "status": svc.status.value, "url": svc.url}
                for name, svc in instance.services.items()
            },
        }
    except RailwayAPIError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/instances/{name}")
async def _delete_instance(name: str):
    try:
        client = _get_client()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, client.force_delete_instance, name)
        return {"deleted": name}
    except RailwayAPIError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Luthien Proxy — Instance Manager</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --bg: #0c0c0e;
    --surface: #141418;
    --surface-raised: #1a1a20;
    --border: #2a2a32;
    --border-subtle: #1e1e26;
    --text: #d4d4d8;
    --text-dim: #71717a;
    --text-bright: #fafafa;
    --amber: #f59e0b;
    --amber-dim: #b45309;
    --amber-glow: rgba(245, 158, 11, 0.12);
    --red: #ef4444;
    --red-dim: rgba(239, 68, 68, 0.15);
    --green: #22c55e;
    --green-dim: rgba(34, 197, 94, 0.15);
    --blue: #3b82f6;
    --mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', monospace;
    --sans: 'DM Sans', system-ui, sans-serif;
  }

  body {
    font-family: var(--sans);
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.5;
  }

  /* Subtle grid background */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(var(--border-subtle) 1px, transparent 1px),
      linear-gradient(90deg, var(--border-subtle) 1px, transparent 1px);
    background-size: 48px 48px;
    opacity: 0.3;
    pointer-events: none;
    z-index: 0;
  }

  .shell {
    position: relative;
    z-index: 1;
    max-width: 960px;
    margin: 0 auto;
    padding: 48px 24px;
  }

  /* Header */
  header {
    margin-bottom: 48px;
  }

  header h1 {
    font-family: var(--mono);
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--amber);
    margin-bottom: 6px;
  }

  header p {
    font-size: 13px;
    color: var(--text-dim);
  }

  .tag {
    display: inline-block;
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 3px;
    background: var(--red-dim);
    color: var(--red);
    margin-left: 12px;
    vertical-align: middle;
  }

  /* Sections */
  section {
    margin-bottom: 40px;
  }

  .section-label {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-subtle);
  }

  /* Create form */
  .create-form {
    display: flex;
    gap: 10px;
    align-items: stretch;
  }

  .create-form input {
    font-family: var(--mono);
    font-size: 14px;
    padding: 10px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-bright);
    outline: none;
    transition: border-color 0.15s;
  }

  .create-form input:focus {
    border-color: var(--amber);
  }

  .create-form input::placeholder {
    color: var(--text-dim);
  }

  #instance-name { flex: 1; }
  #instance-repo { flex: 1; }

  .btn {
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    padding: 10px 20px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }

  .btn-primary {
    background: var(--amber);
    color: #000;
  }
  .btn-primary:hover { background: #fbbf24; }
  .btn-primary:disabled {
    background: var(--amber-dim);
    cursor: not-allowed;
    opacity: 0.6;
  }

  .btn-danger {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 5px 12px;
    font-size: 11px;
  }
  .btn-danger:hover {
    border-color: var(--red);
    color: var(--red);
    background: var(--red-dim);
  }

  /* Status bar (create progress / result) */
  .status-bar {
    margin-top: 16px;
    padding: 14px 16px;
    border-radius: 6px;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1.7;
    display: none;
  }

  .status-bar.visible { display: block; }
  .status-bar.working {
    background: var(--amber-glow);
    border: 1px solid var(--amber-dim);
    color: var(--amber);
  }
  .status-bar.success {
    background: var(--green-dim);
    border: 1px solid rgba(34, 197, 94, 0.3);
    color: var(--green);
  }
  .status-bar.error {
    background: var(--red-dim);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: var(--red);
  }

  .spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid var(--amber-dim);
    border-top-color: var(--amber);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-right: 8px;
    vertical-align: middle;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .key-value { color: var(--text-dim); }
  .key-value strong { color: var(--text); font-weight: 500; }

  /* Instance table */
  .table-wrap {
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    overflow: hidden;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  thead th {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    text-align: left;
    padding: 10px 16px;
    background: var(--surface);
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
  }

  tbody tr {
    border-bottom: 1px solid var(--border-subtle);
    transition: background 0.1s;
  }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: var(--surface); }

  td {
    padding: 12px 16px;
    vertical-align: top;
  }

  .instance-name {
    font-family: var(--mono);
    font-weight: 600;
    color: var(--text-bright);
  }

  .status-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
  }
  .status-dot.running { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .status-dot.deletion_scheduled { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
  .status-dot.failed { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .status-dot.unknown { background: var(--text-dim); }

  .url-link {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--blue);
    text-decoration: none;
  }
  .url-link:hover { text-decoration: underline; }

  .date-cell {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-dim);
  }

  .actions-cell {
    text-align: right;
  }

  .empty-state {
    padding: 48px 16px;
    text-align: center;
    color: var(--text-dim);
    font-size: 13px;
  }

  /* Expanded row detail */
  .detail-row td {
    padding: 0 16px 16px 16px;
    background: var(--surface);
  }
  .detail-row:hover { background: var(--surface); }

  .services-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 10px;
  }

  .svc-card {
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
    border-radius: 6px;
    padding: 10px 14px;
  }

  .svc-card .svc-name {
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 600;
    color: var(--text-bright);
    margin-bottom: 4px;
  }

  .svc-card .svc-status {
    font-size: 11px;
    color: var(--text-dim);
  }

  /* Confirm delete overlay */
  .confirm-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    display: none;
  }
  .confirm-overlay.visible { display: flex; }

  .confirm-box {
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 28px;
    max-width: 400px;
    width: 90%;
  }

  .confirm-box h3 {
    font-family: var(--mono);
    font-size: 14px;
    font-weight: 600;
    color: var(--text-bright);
    margin-bottom: 10px;
  }

  .confirm-box p {
    font-size: 13px;
    color: var(--text-dim);
    margin-bottom: 20px;
  }

  .confirm-box .confirm-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
  }

  .btn-cancel {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 8px 16px;
  }
  .btn-cancel:hover { border-color: var(--text-dim); color: var(--text); }

  .btn-confirm-delete {
    background: var(--red);
    color: #fff;
    padding: 8px 16px;
  }
  .btn-confirm-delete:hover { background: #dc2626; }
</style>
</head>
<body>
<div class="shell">

  <header>
    <h1>Luthien Proxy<span class="tag">Demo</span></h1>
    <p>Provision and manage isolated proxy instances on Railway</p>
  </header>

  <section>
    <div class="section-label">Deploy New Instance</div>
    <div class="create-form">
      <input id="instance-name" type="text" placeholder="instance-name" spellcheck="false" autocomplete="off">
      <input id="instance-repo" type="text" placeholder="owner/repo (optional)" spellcheck="false" autocomplete="off">
      <button class="btn btn-primary" id="create-btn" onclick="createInstance()">Deploy</button>
    </div>
    <div class="status-bar" id="create-status"></div>
  </section>

  <section>
    <div class="section-label">Instances</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Gateway</th>
            <th>Console</th>
            <th>Created</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="instance-list">
          <tr><td colspan="6" class="empty-state">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </section>

</div>

<div class="confirm-overlay" id="confirm-overlay">
  <div class="confirm-box">
    <h3>Delete Instance</h3>
    <p>Permanently delete <strong id="confirm-name"></strong> and all its services? This cannot be undone.</p>
    <div class="confirm-actions">
      <button class="btn btn-cancel" onclick="closeConfirm()">Cancel</button>
      <button class="btn btn-confirm-delete" id="confirm-delete-btn" onclick="confirmDelete()">Delete</button>
    </div>
  </div>
</div>

<script>
let expandedRow = null;
let deleteTarget = null;

async function loadInstances() {
  try {
    const res = await fetch('/api/instances');
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    renderInstances(data.instances);
  } catch (e) {
    document.getElementById('instance-list').innerHTML =
      '<tr><td colspan="6" class="empty-state">Failed to load: ' + esc(e.message) + '</td></tr>';
  }
}

function renderInstances(instances) {
  const tbody = document.getElementById('instance-list');
  if (!instances.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No instances. Deploy one above.</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  for (const inst of instances) {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.onclick = () => toggleDetail(inst, tr);

    const created = inst.created_at
      ? new Date(inst.created_at).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'})
      : '—';

    const gatewayCell = inst.url
      ? '<a class="url-link" href="' + esc(inst.url) + '" target="_blank" onclick="event.stopPropagation()">' + esc(inst.url.replace('https://', '')) + '</a>'
      : '<span style="color:var(--text-dim)">—</span>';

    const consoleCell = inst.railway_url
      ? '<a class="url-link" href="' + esc(inst.railway_url) + '" target="_blank" onclick="event.stopPropagation()">railway.com/project/...</a>'
      : '<span style="color:var(--text-dim)">—</span>';

    const delBtn = document.createElement('button');
    delBtn.className = 'btn btn-danger';
    delBtn.textContent = 'Delete';
    delBtn.onclick = (e) => { e.stopPropagation(); showConfirm(inst.name); };

    tr.innerHTML =
      '<td><span class="instance-name">' + esc(inst.name) + '</span></td>' +
      '<td><span class="status-dot ' + esc(inst.status) + '"></span>' + esc(inst.status.replace('_', ' ')) + '</td>' +
      '<td>' + gatewayCell + '</td>' +
      '<td>' + consoleCell + '</td>' +
      '<td class="date-cell">' + esc(created) + '</td>' +
      '<td class="actions-cell"></td>';
    tr.querySelector('.actions-cell').appendChild(delBtn);
    tbody.appendChild(tr);
  }
}

function toggleDetail(inst, tr) {
  const existing = tr.nextElementSibling;
  if (existing && existing.classList.contains('detail-row')) {
    existing.remove();
    expandedRow = null;
    return;
  }
  // Close any other expanded
  if (expandedRow) { expandedRow.remove(); expandedRow = null; }

  const detail = document.createElement('tr');
  detail.classList.add('detail-row');
  const td = document.createElement('td');
  td.colSpan = 6;

  const services = inst.services || {};
  const keys = Object.keys(services);
  let html = '<div class="services-grid">';
  for (const k of keys) {
    const svc = services[k];
    html += '<div class="svc-card">' +
      '<div class="svc-name"><span class="status-dot ' + esc(svc.status) + '"></span>' + esc(svc.name) + '</div>' +
      '<div class="svc-status">' + esc(svc.status) + '</div>' +
      '</div>';
  }
  if (!keys.length) html += '<span style="color:var(--text-dim);font-size:12px">No service data</span>';
  html += '</div>';

  td.innerHTML = html;
  detail.appendChild(td);
  tr.after(detail);
  expandedRow = detail;
}

async function createInstance() {
  const nameInput = document.getElementById('instance-name');
  const repoInput = document.getElementById('instance-repo');
  const btn = document.getElementById('create-btn');
  const status = document.getElementById('create-status');

  const name = nameInput.value.trim();
  if (!name) { nameInput.focus(); return; }

  btn.disabled = true;
  status.className = 'status-bar visible working';
  status.innerHTML = '<span class="spinner"></span>Provisioning <strong>' + esc(name) + '</strong> &mdash; this takes about a minute...';

  try {
    const body = { name };
    const repo = repoInput.value.trim();
    if (repo) body.repo = repo;

    const res = await fetch('/api/instances', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (data.error) {
      status.className = 'status-bar visible error';
      status.textContent = data.error;
    } else {
      status.className = 'status-bar visible success';
      status.innerHTML =
        '<strong>' + esc(data.name) + '</strong> deployed<br>' +
        '<span class="key-value"><strong>Gateway:</strong> <a class="url-link" href="' + esc(data.url) + '" target="_blank">' + esc(data.url) + '</a></span><br>' +
        '<span class="key-value"><strong>Console:</strong> <a class="url-link" href="' + esc(data.railway_url) + '" target="_blank">' + esc(data.railway_url) + '</a></span><br>' +
        '<span class="key-value"><strong>PROXY_API_KEY:</strong> ' + esc(data.proxy_api_key) + '</span><br>' +
        '<span class="key-value"><strong>ADMIN_API_KEY:</strong> ' + esc(data.admin_api_key) + '</span>';
      nameInput.value = '';
      repoInput.value = '';
      loadInstances();
    }
  } catch (e) {
    status.className = 'status-bar visible error';
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

function showConfirm(name) {
  deleteTarget = name;
  document.getElementById('confirm-name').textContent = name;
  document.getElementById('confirm-overlay').classList.add('visible');
}

function closeConfirm() {
  document.getElementById('confirm-overlay').classList.remove('visible');
  deleteTarget = null;
}

async function confirmDelete() {
  if (!deleteTarget) return;
  const name = deleteTarget;
  const btn = document.getElementById('confirm-delete-btn');
  btn.disabled = true;
  btn.textContent = 'Deleting...';

  try {
    const res = await fetch('/api/instances/' + encodeURIComponent(name), { method: 'DELETE' });
    const data = await res.json();
    if (data.error) alert(data.error);
    loadInstances();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Delete';
    closeConfirm();
  }
}

// Enter key triggers deploy
document.getElementById('instance-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') createInstance();
});

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

loadInstances();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8899)
