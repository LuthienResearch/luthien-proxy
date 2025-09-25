function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') {
      node.className = value;
    } else if (key === 'text') {
      node.textContent = value;
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
  callIds: new Set(),
  messages: [],
  assistant: {
    original: '',
    final: '',
    chunks: [],
    completed: false,
  },
};

let eventSource = null;
let reconnectTimer = null;

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
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function openStream(traceId) {
  closeStream();
  if (!traceId) return;
  setStatus('Listening…', true);
  eventSource = new EventSource(`/api/hooks/conversation/stream_by_trace?trace_id=${encodeURIComponent(traceId)}`);
  eventSource.onmessage = (event) => {
    if (!event.data) return;
    try {
      const payload = JSON.parse(event.data);
      handleEvent(payload);
      setStatus('Live', true);
    } catch (err) {
      console.error('SSE parse error', err);
    }
  };
  eventSource.onerror = () => {
    setStatus('Connection lost, retrying…');
    closeStream();
    reconnectTimer = setTimeout(() => openStream(traceId), 2000);
  };
}

function renderConversation() {
  const container = document.getElementById('chat');
  if (!container) return;
  container.textContent = '';

  const hasAssistant = Boolean(
    state.assistant.original ||
      state.assistant.final ||
      (Array.isArray(state.assistant.chunks) && state.assistant.chunks.length)
  );

  if (!state.messages.length && !hasAssistant) {
    container.appendChild(el('div', { class: 'empty-state', text: 'Select a trace to view the conversation history.' }));
    return;
  }

  for (const message of state.messages) {
    container.appendChild(renderMessage(message));
  }

  if (hasAssistant) {
    container.appendChild(renderAssistantMessage());
  }
}

function renderMessage(message) {
  const wrapper = el('div', { class: 'message role-' + (message.role || 'unknown') });
  const meta = el('div', { class: 'meta' });
  meta.appendChild(el('span', { class: 'badge', text: message.role || 'unknown' }));
  meta.appendChild(el('span', { text: 'Original vs Final' }));
  wrapper.appendChild(meta);

  const versions = el('div', { class: 'versions' });
  const original = el('div', { class: 'version original' });
  original.textContent = message.original || '';
  const modified = (message.final || '') !== (message.original || '');
  const finalClass = 'version final' + (modified ? ' modified' : '');
  const finalNode = el('div', { class: finalClass });
  finalNode.textContent = message.final || message.original || '';
  versions.appendChild(original);
  versions.appendChild(finalNode);
  wrapper.appendChild(versions);
  return wrapper;
}

function renderAssistantMessage() {
  const wrapper = el('div', { class: 'message assistant' });
  const meta = el('div', { class: 'meta' });
  meta.appendChild(el('span', { class: 'badge', text: 'assistant' }));
  const chunkCount = Array.isArray(state.assistant.chunks) ? state.assistant.chunks.length : 0;
  const statusText = state.assistant.completed
    ? `Response complete${chunkCount ? ` • ${chunkCount} chunks` : ''}`
    : chunkCount
      ? `Streaming… ${chunkCount} chunk${chunkCount === 1 ? '' : 's'}`
      : 'Waiting for response';
  meta.appendChild(el('span', { text: statusText }));
  wrapper.appendChild(meta);

  const versions = el('div', { class: 'versions' });
  const original = el('div', { class: 'version original' });
  original.textContent = state.assistant.original || '';
  const modified = (state.assistant.final || '') !== (state.assistant.original || '');
  const finalClass = 'version final' + (modified ? ' modified' : '');
  const finalNode = el('div', { class: finalClass });
  finalNode.textContent = state.assistant.final || state.assistant.original || '';
  versions.appendChild(original);
  versions.appendChild(finalNode);
  wrapper.appendChild(versions);

  if (!state.assistant.completed) {
    wrapper.appendChild(el('div', { class: 'stream-status', text: 'Live updates in progress…' }));
  }

  return wrapper;
}

function applySnapshot(snapshot) {
  if (typeof snapshot.call_id === 'string') {
    state.traceId = snapshot.call_id;
  }
  state.messages = Array.isArray(snapshot.messages) ? snapshot.messages.map((msg) => ({
    role: msg.role || 'unknown',
    original: msg.original || '',
    final: msg.final || msg.original || '',
  })) : [];

  if (Array.isArray(snapshot.call_ids)) {
    state.callIds = new Set(snapshot.call_ids);
  }

  const response = snapshot.response || {};
  const chunks = Array.isArray(response.chunks) ? response.chunks : [];
  state.assistant = {
    original: response.original_text || '',
    final: response.final_text || response.original_text || '',
    chunks: chunks.map((chunk) => ({
      original_delta: chunk.original_delta || '',
      final_delta: chunk.final_delta || chunk.original_delta || '',
      choice_index: chunk.choice_index || 0,
      timestamp: chunk.timestamp || null,
    })),
    completed: Boolean(response.completed),
  };

  if (!state.assistant.original && state.assistant.chunks.length) {
    state.assistant.original = state.assistant.chunks.map((c) => c.original_delta).join('');
  }
  if (!state.assistant.final) {
    if (state.assistant.chunks.length) {
      state.assistant.final = state.assistant.chunks.map((c) => c.final_delta).join('') || state.assistant.original;
    } else {
      state.assistant.final = state.assistant.original;
    }
  }

  renderConversation();
}

function handleEvent(evt) {
  if (!evt) return;
  if (evt.trace_id && state.traceId && evt.trace_id !== state.traceId) return;
  if (!evt.trace_id && evt.call_id && state.callIds.size && !state.callIds.has(evt.call_id)) return;
  switch (evt.type) {
    case 'request': {
      if (!Array.isArray(evt.messages)) return;
      if (evt.call_id) state.callIds.add(evt.call_id);
      state.messages = evt.messages.map((msg) => ({
        role: msg.role || 'unknown',
        original: msg.original || '',
        final: msg.final || msg.original || '',
      }));
      renderConversation();
      break;
    }
    case 'stream': {
      const ts = evt.ts || Date.now() / 1000;
      const replace = Boolean(evt.replace);
      if (evt.call_id) state.callIds.add(evt.call_id);
      if (replace) {
        const last = state.assistant.chunks[state.assistant.chunks.length - 1];
        if (!last) break;
        const previousFinal = last.final_delta || '';
        if (previousFinal) {
          state.assistant.final = state.assistant.final.slice(0, -previousFinal.length);
        }
        const newFinal = evt.final_delta || previousFinal;
        state.assistant.final += newFinal;
        last.final_delta = newFinal;
        last.timestamp = ts;
        state.assistant.completed = false;
        renderConversation();
        break;
      }

      if (evt.call_id) state.callIds.add(evt.call_id);

      const originalDelta = evt.original_delta || '';
      const finalDelta = evt.final_delta || originalDelta;
      state.assistant.original += originalDelta;
      state.assistant.final += finalDelta;
      state.assistant.chunks.push({
        original_delta: originalDelta,
        final_delta: finalDelta,
        choice_index: evt.choice_index || 0,
        timestamp: ts,
      });
      state.assistant.completed = false;
      renderConversation();
      break;
    }
    case 'final': {
      if (evt.call_id) state.callIds.add(evt.call_id);
      if (typeof evt.original_text === 'string' && evt.original_text) {
        state.assistant.original = evt.original_text;
      }
      if (typeof evt.final_text === 'string' && evt.final_text) {
        state.assistant.final = evt.final_text;
      } else if (!state.assistant.final) {
        state.assistant.final = state.assistant.original;
      }
      state.assistant.completed = true;
      renderConversation();
      break;
    }
    default:
      break;
  }
}

async function loadConversation(traceId) {
  if (!traceId) return;
  state.traceId = traceId;
  const active = document.getElementById('active-trace');
  if (active) active.value = traceId;
  setStatus('Loading…');
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation/by_trace?trace_id=${encodeURIComponent(traceId)}`);
    applySnapshot(snapshot);
    if (Array.isArray(snapshot.call_ids)) {
      state.callIds = new Set(snapshot.call_ids);
    } else {
      state.callIds = new Set();
    }
    setStatus('Live', true);
    openStream(traceId);
  } catch (err) {
    console.error('Failed to load conversation', err);
    setStatus('Failed to load');
  }
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
      if (state.traceId) loadConversation(state.traceId);
    });
  }

  loadRecentTraces();
  renderConversation();
  setStatus('Idle');

  window.addEventListener('beforeunload', closeStream);
}

document.addEventListener('DOMContentLoaded', init);
