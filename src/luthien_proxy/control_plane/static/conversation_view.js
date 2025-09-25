function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') {
      node.className = value;
    } else if (key === 'text') {
      node.textContent = value;
    } else if (key === 'dataset' && value && typeof value === 'object') {
      for (const [dataKey, dataValue] of Object.entries(value)) {
        node.dataset[dataKey] = dataValue;
      }
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children) {
    if (child == null) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  return await res.json();
}

const state = {
  callId: null,
  traceId: null,
  call: null,
  eventSource: null,
  reconnectTimer: null,
  pendingRefresh: null,
};

function setStatus(text, live = false) {
  const statusEl = document.getElementById('status');
  if (!statusEl) return;
  statusEl.textContent = text;
  if (live) {
    statusEl.classList.add('live');
  } else {
    statusEl.classList.remove('live');
  }
}

function closeStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function scheduleRefresh() {
  if (!state.callId) return;
  if (state.pendingRefresh) return;
  state.pendingRefresh = setTimeout(async () => {
    state.pendingRefresh = null;
    try {
      await hydrateCall(state.callId, { preserveStatus: true });
    } catch (err) {
      console.error('Failed to refresh call snapshot', err);
    }
  }, 200);
}

function openStream(callId) {
  closeStream();
  if (!callId) return;
  setStatus('Listening…', true);
  state.eventSource = new EventSource(`/api/hooks/conversation/stream?call_id=${encodeURIComponent(callId)}`);
  state.eventSource.onmessage = () => {
    setStatus('Live', true);
    scheduleRefresh();
  };
  state.eventSource.onerror = () => {
    setStatus('Connection lost, retrying…');
    closeStream();
    state.reconnectTimer = setTimeout(() => openStream(callId), 2000);
  };
}

function statusBadge(call) {
  switch (call.status) {
    case 'success':
    case 'stream_summary':
      return { key: 'success', label: 'Completed' };
    case 'failure':
      return { key: 'failure', label: 'Failed' };
    case 'streaming':
      return { key: 'streaming', label: 'Streaming' };
    default:
      return { key: 'pending', label: 'Pending' };
  }
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString();
}

function renderMessageDiff(diff) {
  const wrapper = el('div', { class: 'message role-' + (diff.role || 'unknown') });
  const meta = el('div', { class: 'meta' });
  meta.appendChild(el('span', { class: 'badge', text: diff.role || 'unknown' }));
  meta.appendChild(el('span', { text: 'Original vs Final' }));
  wrapper.appendChild(meta);

  const versions = el('div', { class: 'versions' });
  const original = el('div', { class: 'version original' });
  original.textContent = diff.original || '';
  const modified = (diff.final || '') !== (diff.original || '');
  const finalNode = el('div', { class: 'version final' + (modified ? ' modified' : '') });
  finalNode.textContent = diff.final || diff.original || '';
  versions.appendChild(original);
  versions.appendChild(finalNode);
  wrapper.appendChild(versions);
  return wrapper;
}

function renderResponse(call) {
  const wrapper = el('div', { class: 'message assistant' });
  const meta = el('div', { class: 'meta' });
  meta.appendChild(el('span', { class: 'badge', text: 'assistant' }));
  let statusText = 'Waiting for response';
  if (call.status === 'failure') {
    statusText = 'Response failed';
  } else if (call.status === 'streaming') {
    const chunkCount = call.chunk_count || 0;
    statusText = chunkCount ? `Streaming… ${chunkCount} chunk${chunkCount === 1 ? '' : 's'}` : 'Streaming…';
  } else if (call.status === 'stream_summary') {
    statusText = 'Stream summary received';
  } else if (call.final_response || call.original_response) {
    statusText = 'Response complete';
  }
  meta.appendChild(el('span', { text: statusText }));
  wrapper.appendChild(meta);

  const versions = el('div', { class: 'versions' });
  const original = el('div', { class: 'version original' });
  original.textContent = call.original_response || '';
  const modified = (call.final_response || '') !== (call.original_response || '');
  const finalNode = el('div', { class: 'version final' + (modified ? ' modified' : '') });
  finalNode.textContent = call.final_response || call.original_response || '';
  versions.appendChild(original);
  versions.appendChild(finalNode);
  wrapper.appendChild(versions);

  if (call.status === 'streaming') {
    wrapper.appendChild(el('div', { class: 'stream-status', text: 'Live updates in progress…' }));
  } else if (call.status === 'failure') {
    wrapper.appendChild(el('div', { class: 'stream-status', text: 'Policy marked this call as failed.' }));
  }

  return wrapper;
}

function renderConversation() {
  const container = document.getElementById('chat');
  if (!container) return;
  container.textContent = '';

  if (!state.call) {
    container.appendChild(el('div', { class: 'empty-state', text: 'Select a call to view the conversation trace.' }));
    return;
  }

  const call = state.call;
  const wrapper = el('div', { class: 'call' });
  const header = el('div', { class: 'call-header' });
  header.appendChild(el('div', { class: 'call-title', text: call.call_id }));
  const badge = statusBadge(call);
  header.appendChild(el('span', { class: `badge status-${badge.key}`, text: badge.label }));
  const metaBits = [];
  const started = formatDate(call.started_at);
  if (started) metaBits.push(`Started ${started}`);
  const finished = formatDate(call.completed_at);
  if (finished) metaBits.push(`Finished ${finished}`);
  if (metaBits.length) {
    header.appendChild(el('div', { class: 'call-meta', text: metaBits.join(' • ') }));
  }
  wrapper.appendChild(header);

  const body = el('div', { class: 'call-body' });
  const requestSection = el('div', { class: 'call-section' });
  requestSection.appendChild(el('div', { class: 'section-title', text: 'Request Messages' }));
  const messagesContainer = el('div', { class: 'call-messages' });
  if (!call.new_messages || call.new_messages.length === 0) {
    messagesContainer.appendChild(el('div', { class: 'empty-state inline', text: 'No new messages in this turn.' }));
  } else {
    for (const diff of call.new_messages) {
      messagesContainer.appendChild(renderMessageDiff(diff));
    }
  }
  requestSection.appendChild(messagesContainer);
  body.appendChild(requestSection);

  const responseSection = el('div', { class: 'call-section' });
  responseSection.appendChild(el('div', { class: 'section-title', text: 'Assistant Output' }));
  responseSection.appendChild(renderResponse(call));
  body.appendChild(responseSection);

  wrapper.appendChild(body);
  container.appendChild(wrapper);
}

async function hydrateCall(callId, options = {}) {
  if (!callId) return;
  if (!options.preserveStatus) {
    setStatus('Loading…');
  }
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation?call_id=${encodeURIComponent(callId)}`);
    state.traceId = snapshot.trace_id || state.traceId;
    state.call = Array.isArray(snapshot.calls) && snapshot.calls.length ? snapshot.calls[0] : null;
    renderConversation();
    setStatus('Live', true);
  } catch (err) {
    console.error('Failed to load conversation', err);
    setStatus('Failed to load');
  }
}

async function loadConversation(callId) {
  if (!callId) return;
  state.callId = callId;
  const active = document.getElementById('active-cid');
  if (active) active.value = callId;
  closeStream();
  await hydrateCall(callId);
  openStream(callId);
}

async function loadRecentCallIds() {
  const list = document.getElementById('cid-list');
  if (!list) return;
  list.textContent = 'Loading…';
  try {
    const items = await fetchJSON('/api/hooks/recent_call_ids?limit=50');
    if (!Array.isArray(items) || items.length === 0) {
      list.textContent = 'No recent call IDs';
      return;
    }
    list.textContent = '';
    for (const item of items) {
      const row = el('div', { class: 'cid-item' });
      const inner = el('div');
      inner.appendChild(el('div', { class: 'cid', text: item.call_id }));
      const metaParts = [];
      if (item.latest) {
        metaParts.push(new Date(item.latest).toLocaleString());
      }
      metaParts.push(`${item.count} event${item.count === 1 ? '' : 's'}`);
      inner.appendChild(el('div', { class: 'meta', text: metaParts.join(' • ') }));
      const btn = el('button', { class: 'secondary', text: 'View' });
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        loadConversation(item.call_id);
      });
      row.appendChild(inner);
      row.appendChild(btn);
      row.addEventListener('click', () => loadConversation(item.call_id));
      list.appendChild(row);
    }
  } catch (err) {
    console.error('Failed to load call IDs', err);
    list.textContent = 'Failed to load recent call IDs';
  }
}

function init() {
  const loadBtn = document.getElementById('cid-load');
  if (loadBtn) {
    loadBtn.addEventListener('click', () => {
      const input = document.getElementById('cid-input');
      const value = input ? input.value.trim() : '';
      if (value) loadConversation(value);
    });
  }

  const refreshBtn = document.getElementById('refresh-cids');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadRecentCallIds());
  }

  const disconnectBtn = document.getElementById('disconnect');
  if (disconnectBtn) {
    disconnectBtn.addEventListener('click', () => {
      closeStream();
      setStatus('Paused');
    });
  }

  const refreshConvBtn = document.getElementById('refresh-conv');
  if (refreshConvBtn) {
    refreshConvBtn.addEventListener('click', () => {
      if (state.callId) hydrateCall(state.callId, { preserveStatus: true });
    });
  }

  loadRecentCallIds();
  renderConversation();
  setStatus('Idle');

  window.addEventListener('beforeunload', closeStream);
}

document.addEventListener('DOMContentLoaded', init);
