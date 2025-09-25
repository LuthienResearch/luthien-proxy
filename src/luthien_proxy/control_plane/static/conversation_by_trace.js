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
  traceId: null,
  calls: [],
  callMap: new Map(),
  eventSource: null,
  reconnectTimer: null,
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
    const chunkCount = call.final_chunks.length;
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

function renderCall(call) {
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
  return wrapper;
}

function renderConversation() {
  const container = document.getElementById('chat');
  if (!container) return;
  container.textContent = '';

  if (!state.calls.length) {
    container.appendChild(el('div', { class: 'empty-state', text: 'Select a trace to view the conversation history.' }));
    return;
  }

  for (const call of state.calls) {
    container.appendChild(renderCall(call));
  }
}

function adoptCallSnapshot(snapshotCall) {
  return {
    call_id: snapshotCall.call_id,
    trace_id: snapshotCall.trace_id || state.traceId,
    started_at: snapshotCall.started_at || null,
    completed_at: snapshotCall.completed_at || null,
    status: snapshotCall.status || 'pending',
    new_messages: Array.isArray(snapshotCall.new_messages) ? snapshotCall.new_messages : [],
    original_response: snapshotCall.original_response || '',
    final_response: snapshotCall.final_response || '',
    original_chunks: Array.isArray(snapshotCall.original_chunks)
      ? [...snapshotCall.original_chunks]
      : snapshotCall.original_response
        ? [snapshotCall.original_response]
        : [],
    final_chunks: Array.isArray(snapshotCall.final_chunks)
      ? [...snapshotCall.final_chunks]
      : snapshotCall.final_response
        ? [snapshotCall.final_response]
        : [],
  };
}

function rebuildCallState(calls) {
  state.calls = calls.map(adoptCallSnapshot);
  state.callMap = new Map();
  for (const call of state.calls) {
    state.callMap.set(call.call_id, call);
  }
  renderConversation();
}

async function hydrateTrace(traceId, options = {}) {
  if (!traceId) return;
  if (!options.preserveStatus) {
    setStatus('Loading…');
  }
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation/by_trace?trace_id=${encodeURIComponent(traceId)}`);
    state.traceId = snapshot.trace_id || traceId;
    const calls = Array.isArray(snapshot.calls) ? snapshot.calls : [];
    rebuildCallState(calls);
    setStatus('Live', true);
  } catch (err) {
    console.error('Failed to load conversation', err);
    setStatus('Failed to load');
  }
}

function ensureCall(callId) {
  let call = state.callMap.get(callId);
  if (!call) {
    call = {
      call_id: callId,
      trace_id: state.traceId,
      started_at: null,
      completed_at: null,
      status: 'pending',
      new_messages: [],
      original_response: '',
      final_response: '',
      original_chunks: [],
      final_chunks: [],
    };
    state.calls.push(call);
    state.callMap.set(callId, call);
  }
  return call;
}

function applyChunk(call, payload, streamKey) {
  const delta = typeof payload.delta === 'string' ? payload.delta : '';
  if (!delta) return;
  const chunkIndex = typeof payload.chunk_index === 'number' ? payload.chunk_index : null;
  const targetKey = streamKey === 'final' ? 'final_chunks' : 'original_chunks';
  const chunks = call[targetKey];
  let index = chunkIndex;
  if (index == null || index > chunks.length) {
    index = chunks.length;
  }
  if (index === chunks.length) {
    chunks.push(delta);
  } else if (chunks[index] !== delta) {
    chunks[index] = delta;
  } else {
    return;
  }
  const text = chunks.join('');
  if (streamKey === 'final') {
    call.final_response = text;
    call.status = call.status === 'pending' ? 'streaming' : call.status;
  } else {
    call.original_response = text;
  }
}

function handleEvent(evt) {
  if (!evt || !evt.call_id) return;
  if (evt.trace_id && !state.traceId) {
    state.traceId = evt.trace_id;
  }

  switch (evt.event_type) {
    case 'request_started': {
      const call = ensureCall(evt.call_id);
      call.started_at = evt.timestamp || call.started_at;
      call.completed_at = null;
      call.status = 'streaming';
      call.new_messages = [];
      call.original_response = '';
      call.final_response = '';
      call.original_chunks = [];
      call.final_chunks = [];
      hydrateTrace(state.traceId, { preserveStatus: true });
      break;
    }
    case 'original_chunk': {
      const call = ensureCall(evt.call_id);
      applyChunk(call, evt.payload || {}, 'original');
      break;
    }
    case 'final_chunk': {
      const call = ensureCall(evt.call_id);
      applyChunk(call, evt.payload || {}, 'final');
      break;
    }
    case 'request_completed': {
      const call = ensureCall(evt.call_id);
      const payload = evt.payload || {};
      if (payload.original_response) {
        call.original_chunks = [payload.original_response];
        call.original_response = payload.original_response;
      }
      if (payload.final_response) {
        call.final_chunks = [payload.final_response];
        call.final_response = payload.final_response;
      }
      call.status = payload.status || 'success';
      call.completed_at = evt.timestamp || call.completed_at;
      hydrateTrace(state.traceId, { preserveStatus: true });
      break;
    }
    default:
      break;
  }

  renderConversation();
}

function openStream(traceId) {
  closeStream();
  if (!traceId) return;
  setStatus('Listening…', true);
  const url = `/api/hooks/conversation/stream_by_trace?trace_id=${encodeURIComponent(traceId)}`;
  state.eventSource = new EventSource(url);
  state.eventSource.onmessage = (event) => {
    if (!event.data) return;
    try {
      const payload = JSON.parse(event.data);
      handleEvent(payload);
      setStatus('Live', true);
    } catch (err) {
      console.error('SSE parse error', err);
    }
  };
  state.eventSource.onerror = () => {
    setStatus('Connection lost, retrying…');
    closeStream();
    state.reconnectTimer = setTimeout(() => openStream(traceId), 2000);
  };
}

async function loadConversation(traceId) {
  if (!traceId) return;
  state.traceId = traceId;
  const active = document.getElementById('active-trace');
  if (active) active.value = traceId;
  closeStream();
  await hydrateTrace(traceId);
  openStream(traceId);
}

async function loadRecentTraces() {
  const list = document.getElementById('trace-list');
  if (!list) return;
  list.textContent = 'Loading…';
  try {
    const items = await fetchJSON('/api/hooks/recent_traces?limit=50');
    if (!Array.isArray(items) || items.length === 0) {
      list.textContent = 'No recent trace IDs';
      return;
    }
    list.textContent = '';
    for (const item of items) {
      const row = el('div', { class: 'cid-item' });
      const inner = el('div');
      inner.appendChild(el('div', { class: 'cid', text: item.trace_id }));
      const metaParts = [];
      if (item.latest) {
        metaParts.push(new Date(item.latest).toLocaleString());
      }
      metaParts.push(`${item.call_count} call${item.call_count === 1 ? '' : 's'}`);
      metaParts.push(`${item.event_count} events`);
      inner.appendChild(el('div', { class: 'meta', text: metaParts.join(' • ') }));
      const btn = el('button', { class: 'secondary', text: 'View' });
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        loadConversation(item.trace_id);
      });
      row.appendChild(inner);
      row.appendChild(btn);
      row.addEventListener('click', () => loadConversation(item.trace_id));
      list.appendChild(row);
    }
  } catch (err) {
    console.error('Failed to load trace IDs', err);
    list.textContent = 'Failed to load recent trace IDs';
  }
}

function init() {
  const loadBtn = document.getElementById('trace-load');
  if (loadBtn) {
    loadBtn.addEventListener('click', () => {
      const input = document.getElementById('trace-input');
      const value = input ? input.value.trim() : '';
      if (value) loadConversation(value);
    });
  }

  const refreshBtn = document.getElementById('refresh-traces');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadRecentTraces());
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
      if (state.traceId) hydrateTrace(state.traceId, { preserveStatus: true });
    });
  }

  loadRecentTraces();
  renderConversation();
  setStatus('Idle');

  window.addEventListener('beforeunload', closeStream);
}

document.addEventListener('DOMContentLoaded', init);
