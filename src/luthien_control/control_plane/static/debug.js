// Shared JSON renderer with recursive collapsibility, gutter arrows, color coding

function span(cls, text) { const s = document.createElement('span'); if (cls) s.className = cls; s.textContent = text; return s; }
function renderPrimitive(val) {
  if (val === null) return span('json-null', 'null');
  if (typeof val === 'string') return span('json-string', JSON.stringify(val));
  if (typeof val === 'number') return span('json-number', String(val));
  if (typeof val === 'boolean') return span('json-bool', String(val));
  return span('', JSON.stringify(val));
}

// Block-structured renderer with consistent indentation and inline ellipsis

const INDENT_PX = 16;

function makeLine(depth, hasArrow = false) {
  const line = document.createElement('div');
  line.className = 'json-line';
  const arrow = document.createElement('span');
  arrow.className = 'collapser' + (hasArrow ? '' : ' hidden');
  arrow.textContent = '▼';
  const content = document.createElement('span');
  content.className = 'json-content';
  content.style.paddingLeft = (depth * INDENT_PX) + 'px';
  line.appendChild(arrow);
  line.appendChild(content);
  return { line, arrow, content };
}

function renderObject(obj, isArray, depth = 0, key = null, isLast = true) {
  const container = document.createElement('div');
  container.className = 'json-block';

  // Header line
  const { line: header, arrow, content: hc } = makeLine(depth, true);
  if (key !== null) { hc.appendChild(span('json-key', JSON.stringify(key))); hc.appendChild(span('', ': ')); }
  const openB = span('bracket', isArray ? '[' : '{');
  const inlineEllipsis = span('ellipsis', isArray ? '[…]' : '{…}'); inlineEllipsis.style.display = 'none';
  hc.appendChild(openB);
  hc.appendChild(inlineEllipsis);
  container.appendChild(header);

  // Children container
  const children = document.createElement('div');
  children.className = 'json-children';
  container.appendChild(children);

  const keys = isArray ? obj.map((_, i) => i) : Object.keys(obj);
  const getVal = (k) => isArray ? obj[k] : obj[k];
  const lastIdx = keys.length - 1;
  keys.forEach((k, i) => {
    const v = getVal(k);
    const last = i === lastIdx;
    if (v && typeof v === 'object') {
      children.appendChild(renderObject(v, Array.isArray(v), depth + 1, isArray ? null : k, last));
    } else {
      const { line, content } = makeLine(depth + 1, false);
      if (!isArray) { content.appendChild(span('json-key', JSON.stringify(k))); content.appendChild(span('', ': ')); }
      renderJsonInto(v, content, depth + 1);
      if (!last) content.appendChild(span('comma', ','));
      children.appendChild(line);
    }
  });

  // Closing line
  const { line: closing, content: cc } = makeLine(depth, false);
  cc.appendChild(span('bracket', isArray ? ']' : '}'));
  if (!isLast) cc.appendChild(span('comma', ','));
  container.appendChild(closing);

  // Toggle behavior
  function setExpanded(expanded) {
    if (expanded) {
      children.style.display = '';
      closing.style.display = '';
      inlineEllipsis.style.display = 'none';
      arrow.textContent = '▼';
      openB.style.display = 'inline';
    } else {
      children.style.display = 'none';
      closing.style.display = 'none';
      inlineEllipsis.style.display = '';
      arrow.textContent = '▶';
      openB.style.display = 'none';
    }
  }
  header.addEventListener('click', () => { const expanded = children.style.display !== 'none'; setExpanded(!expanded); });
  arrow.addEventListener('click', (e) => { e.stopPropagation(); const expanded = children.style.display !== 'none'; setExpanded(!expanded); });

  return container;
}

function renderJsonInto(val, parent, depth) {
  if (val && typeof val === 'object') {
    parent.appendChild(renderObject(val, Array.isArray(val), depth));
  } else {
    parent.appendChild(renderPrimitive(val));
  }
}

function renderJson(value, parent) {
  parent.textContent = '';
  const isArr = Array.isArray(value);
  parent.appendChild(renderObject(value, isArr, 0, null, true));
}

async function loadTypesInto(selectEl) {
  selectEl.innerHTML = '';
  let types = [];
  try {
    const res = await fetch('/api/debug/types');
    if (res.ok) types = await res.json();
  } catch {}
  if (!Array.isArray(types) || types.length === 0) {
    types = [
      {debug_type_identifier: 'kwargs_pre', count: 0},
      {debug_type_identifier: 'kwargs_post', count: 0},
      {debug_type_identifier: 'stream_chunk', count: 0},
    ];
  }
  for (const t of types) {
    const opt = document.createElement('option');
    opt.value = t.debug_type_identifier;
    opt.textContent = t.debug_type_identifier + (t.count ? (' (' + t.count + ')') : '');
    selectEl.appendChild(opt);
  }
  if (types.length > 0) selectEl.value = types[0].debug_type_identifier;
}

async function initBrowser() {
  const typeSel = document.getElementById('type');
  await loadTypesInto(typeSel);

  let currentPage = 1;
  async function loadPage() {
    const pageSize = parseInt(document.getElementById('pageSize').value || '20');
    const type = typeSel.value;
    const err = document.getElementById('error');
    err.style.display = 'none';
    const url = `/api/debug/${encodeURIComponent(type)}/page?page=${currentPage}&page_size=${pageSize}`;
    let data;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('Fetch failed: ' + res.status);
      data = await res.json();
    } catch (e) {
      err.textContent = 'Error: ' + e.message;
      err.style.display = 'block';
      return;
    }
    const list = document.getElementById('list');
    list.innerHTML = '';
    if (!data || !Array.isArray(data.items)) return;

    let currentItems = data.items;

    function renderList() {
      list.innerHTML = '';
      const q = (document.getElementById('filter').value || '').toLowerCase();
      for (const item of currentItems) {
        const text = JSON.stringify(item.jsonblob).toLowerCase();
        if (q && !text.includes(q)) continue;
        const container = document.createElement('div');
        container.className = 'entry';
        const header = document.createElement('div'); header.className = 'header';
        const left = document.createElement('div'); left.textContent = item.debug_type_identifier + ' • ' + item.id;
        const right = document.createElement('div'); right.textContent = new Date(item.time_created).toLocaleString();
        header.appendChild(left); header.appendChild(right);
        const content = document.createElement('div'); content.className = 'content';
        const viewer = document.createElement('div'); viewer.className = 'json-view';
        renderJson(item.jsonblob, viewer);
        content.appendChild(viewer);
        container.appendChild(header); container.appendChild(content);
        list.appendChild(container);
      }
    }

    renderList();
    const filterInput = document.getElementById('filter');
    if (filterInput && !filterInput.dataset.bound) {
      filterInput.addEventListener('input', renderList);
      filterInput.dataset.bound = '1';
    }
    const totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 1)));
    document.getElementById('pageInfo').textContent = `Page ${data.page} / ${totalPages} • ${data.total} items`;
    document.getElementById('prev').disabled = (data.page <= 1);
    document.getElementById('next').disabled = (data.page >= totalPages);
  }

  document.getElementById('prev').addEventListener('click', () => { if (currentPage > 1) { currentPage -= 1; loadPage(); }});
  document.getElementById('next').addEventListener('click', () => { currentPage += 1; loadPage(); });
  document.getElementById('refresh').addEventListener('click', () => { currentPage = 1; loadPage(); });
  typeSel.addEventListener('change', () => { currentPage = 1; loadPage(); });
  document.getElementById('pageSize').addEventListener('change', () => { currentPage = 1; loadPage(); });
  await loadPage();
}

async function initSingle(type) {
  const limitInput = document.getElementById('limit');
  const list = document.getElementById('list');
  async function load() {
    const limit = parseInt(limitInput.value || '50');
    const res = await fetch(`/api/debug/${encodeURIComponent(type)}?limit=${limit}`);
    const data = await res.json();
    list.innerHTML = '';
    const items = Array.isArray(data) ? data : [];
    const qEl = document.getElementById('filter');

    function renderList() {
      list.innerHTML = '';
      const q = (qEl?.value || '').toLowerCase();
      for (const item of items) {
        const text = JSON.stringify(item.jsonblob).toLowerCase();
        if (q && !text.includes(q)) continue;
        const container = document.createElement('div'); container.className = 'entry';
        const header = document.createElement('div'); header.className = 'header';
        const left = document.createElement('div'); left.innerHTML = '<strong>ID:</strong> ' + item.id;
        const right = document.createElement('div'); right.textContent = new Date(item.time_created).toLocaleString();
        header.appendChild(left); header.appendChild(right);
        const content = document.createElement('div'); content.className = 'content';
        const viewer = document.createElement('div'); viewer.className = 'json-view';
        renderJson(item.jsonblob, viewer);
        content.appendChild(viewer);
        container.appendChild(header); container.appendChild(content);
        list.appendChild(container);
      }
    }

    renderList();
    if (qEl && !qEl.dataset.bound) { qEl.addEventListener('input', renderList); qEl.dataset.bound = '1'; }
  }
  document.getElementById('refresh').addEventListener('click', load);
  await load();
}

// Entry
window.addEventListener('DOMContentLoaded', async () => {
  if (window.DEBUG_TYPE) {
    await initSingle(window.DEBUG_TYPE);
  } else if (document.getElementById('debug-browser')) {
    await initBrowser();
  }
});
