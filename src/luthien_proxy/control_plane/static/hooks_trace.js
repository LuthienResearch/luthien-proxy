async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
}

function el(tag, attrs={}, ...children) {
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else e.setAttribute(k, v);
  }
  for (const c of children) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return e;
}

async function loadRecentCIDs() {
  const list = document.getElementById('cid-list');
  list.textContent = 'Loading…';
  try {
    const items = await fetchJSON('/api/hooks/recent_call_ids?limit=50');
    if (!Array.isArray(items) || items.length === 0) { list.textContent = 'No recent call IDs'; return; }
    list.textContent = '';
    for (const it of items) {
      const row = el('div', {class: 'cid-item'});
      const left = el('div');
      left.appendChild(el('div', {class:'cid', text: it.call_id}));
      left.appendChild(el('div', {class:'meta', text: new Date(it.latest).toLocaleString() + ' • ' + it.count + ' events'}));
      const btn = el('button', {class:'pill'}, 'Load');
      btn.addEventListener('click', () => loadTrace(it.call_id));
      row.append(left, btn);
      row.addEventListener('click', (e)=>{ if (e.target!==btn) loadTrace(it.call_id); });
      list.appendChild(row);
    }
  } catch (e) {
    list.textContent = 'Failed to load recent IDs';
  }
}

function renderTimeline(data) {
  const t = document.getElementById('timeline');
  t.textContent = '';
  if (!data || !Array.isArray(data.entries) || data.entries.length === 0) { t.textContent = 'No entries for this call_id'; return; }
  // compute baseline (earliest) in ns
  const toNs = (e) => (e.post_time_ns && typeof e.post_time_ns === 'number') ? e.post_time_ns : (Date.parse(e.time) * 1e6);
  let minNs = Infinity;
  for (const e of data.entries) {
    const ns = toNs(e);
    if (!Number.isNaN(ns) && ns < minNs) minNs = ns;
  }
  // helper: format delta ns as seconds with millisecond precision
  const formatDeltaNs = (ns) => {
    if (!Number.isFinite(ns)) return 'n/a';
    const seconds = Math.max(0, ns) / 1e9;
    return `${seconds.toFixed(3)} s`;
  };

  for (const entry of data.entries) {
    const head = el('div', {class:'head'});
    const nsVal = toNs(entry);
    const deltaNs = (!Number.isNaN(nsVal) && isFinite(minNs)) ? (nsVal - minNs) : undefined;
    const ms = nsVal / 1e6;
    const when = Number.isFinite(ms) ? new Date(ms).toLocaleString() : '';
    const deltaStr = (deltaNs !== undefined) ? formatDeltaNs(deltaNs) : 'n/a';
    const parts = [];
    // prefer payload.when if present (pre/post/stream)
    const payloadWhen = entry && entry.payload && entry.payload.when;
    if (payloadWhen) parts.push(String(payloadWhen));
    parts.push(`Δt=${deltaStr}`);
    if (when) parts.push(when);
    let label = '(missing debug_type)';
    if (entry && entry.debug_type) {
      label = entry.debug_type.startsWith('hook:') ? entry.debug_type.slice(5) : entry.debug_type;
    }
    head.append(
      el('span', {class:'pill'}, label),
      el('span', {class:'src'}, parts.join(' • ')),
    );
    const details = el('details');
    const summary = el('summary', {text: 'Details'});
    const pre = el('pre');
    pre.textContent = JSON.stringify(entry.payload, null, 2);
    details.append(summary, pre);
    const e = el('div', {class:'entry'});
    e.append(head, details);
    t.appendChild(e);
  }
}

async function loadTrace(cid) {
  const input = document.getElementById('active-cid');
  input.value = cid;
  const data = await fetchJSON(`/api/hooks/trace_by_call_id?call_id=${encodeURIComponent(cid)}`);
  renderTimeline(data);
}

function init() {
  document.getElementById('cid-load').addEventListener('click', () => {
    const cid = document.getElementById('cid-input').value.trim();
    if (cid) loadTrace(cid);
  });
  document.getElementById('refresh').addEventListener('click', () => {
    const cid = document.getElementById('active-cid').value.trim();
    if (cid) loadTrace(cid);
  });
  loadRecentCIDs();
}

document.addEventListener('DOMContentLoaded', init);
