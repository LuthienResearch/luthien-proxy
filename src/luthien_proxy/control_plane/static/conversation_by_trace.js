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
  calls: new Map(),
  callOrder: [],
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
      consumeEvent(payload);
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

function parseTimestamp(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function createCallState(callId) {
  return {
    callId,
    request: {
      original: [],
      final: [],
    },
    originalText: '',
    finalText: '',
    originalChunks: [],
    finalChunks: [],
    status: 'pending',
    completed: false,
    startedAt: null,
    finishedAt: null,
    firstSequence: null,
    lastSequence: null,
    events: [],
    eventKeys: new Set(),
  };
}

function ensureCall(callId) {
  if (!callId) return null;
  let call = state.calls.get(callId);
  if (!call) {
    call = createCallState(callId);
    state.calls.set(callId, call);
    if (!state.callOrder.includes(callId)) {
      state.callOrder.push(callId);
    }
  } else if (!state.callOrder.includes(callId)) {
    state.callOrder.push(callId);
  }
  return call;
}

function normalizeMessages(messages) {
  if (!Array.isArray(messages)) return [];
  return messages.map((msg) => ({
    role: typeof msg?.role === 'string' ? msg.role : 'unknown',
    content: typeof msg?.content === 'string' ? msg.content : '',
  }));
}

function cloneMessages(messages) {
  return messages.map((msg) => ({ ...msg }));
}

function compareEvents(a, b) {
  const seqA = typeof a.sequence === 'number' ? a.sequence : Number.POSITIVE_INFINITY;
  const seqB = typeof b.sequence === 'number' ? b.sequence : Number.POSITIVE_INFINITY;
  if (seqA !== seqB) return seqA - seqB;
  const tsA = parseTimestamp(a.timestamp)?.getTime() ?? Number.POSITIVE_INFINITY;
  const tsB = parseTimestamp(b.timestamp)?.getTime() ?? Number.POSITIVE_INFINITY;
  if (tsA !== tsB) return tsA - tsB;
  return (a.event_type || '').localeCompare(b.event_type || '');
}

function resetCallDerived(call) {
  call.request.original = [];
  call.request.final = [];
  call.originalText = '';
  call.finalText = '';
  call.originalChunks = [];
  call.finalChunks = [];
  call.status = 'pending';
  call.completed = false;
  call.startedAt = null;
  call.finishedAt = null;
  call.firstSequence = null;
  call.lastSequence = null;
}

function applyEventToCall(call, evt) {
  const sequence = typeof evt.sequence === 'number' ? evt.sequence : null;
  if (sequence !== null) {
    if (call.firstSequence == null || sequence < call.firstSequence) {
      call.firstSequence = sequence;
    }
    if (call.lastSequence == null || sequence > call.lastSequence) {
      call.lastSequence = sequence;
    }
  }

  const timestamp = parseTimestamp(evt.timestamp);
  const payload = evt.payload || {};

  switch (evt.event_type) {
    case 'request_started':
      handleRequestStarted(call, payload, timestamp);
      break;
    case 'original_chunk':
      handleOriginalChunk(call, payload, timestamp);
      break;
    case 'final_chunk':
      handleFinalChunk(call, payload, timestamp);
      break;
    case 'request_completed':
      handleRequestCompleted(call, payload, timestamp);
      break;
    default:
      break;
  }
}

function rebuildCallFromEvents(call) {
  resetCallDerived(call);
  const orderedEvents = [...call.events].sort(compareEvents);
  for (const event of orderedEvents) {
    applyEventToCall(call, event);
  }
  if (!call.finalText) {
    call.finalText = call.originalText;
  }
}

function buildMessagePairs(call, contextMessages) {
  const originals = call.request.original;
  const finals = call.request.final.length ? call.request.final : call.request.original;
  const baseline = contextMessages || [];
  const maxLen = Math.max(originals.length, finals.length, baseline.length);
  const pairs = [];

  for (let idx = 0; idx < maxLen; idx += 1) {
    const original = originals[idx] || null;
    const finalMsg = finals[idx] || null;
    const contextMsg = baseline[idx] || null;
    const role = finalMsg?.role || original?.role || contextMsg?.role || 'unknown';
    const originalText = original?.content || '';
    const finalText = finalMsg?.content || originalText;
    const contextText = contextMsg?.content ?? null;

    const unchangedFromContext =
      contextMsg !== null && originalText === contextText && finalText === contextText;
    if (unchangedFromContext) {
      continue;
    }

    pairs.push({ role, original: originalText, final: finalText });
  }

  return pairs;
}

function assistantStatusText(call) {
  if (call.completed) {
    if (call.status === 'failure') return 'Response failed';
    if (call.status === 'stream_summary') return 'Stream summary received';
    return 'Response complete';
  }
  const chunkCount = call.finalChunks.length || call.originalChunks.length;
  if (chunkCount) {
    return `Streaming… ${chunkCount} chunk${chunkCount === 1 ? '' : 's'}`;
  }
  return 'Waiting for response';
}

function statusBadge(call) {
  if (call.status === 'failure') return { key: 'failure', label: 'Failed' };
  if (call.status === 'stream_summary') return { key: 'summary', label: 'Summary' };
  if (call.completed) return { key: 'success', label: 'Completed' };
  if (call.status === 'streaming') return { key: 'streaming', label: 'Streaming' };
  return { key: 'pending', label: 'Pending' };
}

function renderMessagePair(message) {
  const wrapper = el('div', { class: 'message role-' + (message.role || 'unknown') });
  const meta = el('div', { class: 'meta' });
  meta.appendChild(el('span', { class: 'badge', text: message.role || 'unknown' }));
  meta.appendChild(el('span', { text: 'Original vs Final' }));
  wrapper.appendChild(meta);

  const versions = el('div', { class: 'versions' });
  const original = el('div', { class: 'version original' });
  original.textContent = message.original || '';
  const modified = (message.final || '') !== (message.original || '');
  const finalNode = el('div', { class: 'version final' + (modified ? ' modified' : '') });
  finalNode.textContent = message.final || message.original || '';
  versions.appendChild(original);
  versions.appendChild(finalNode);
  wrapper.appendChild(versions);
  return wrapper;
}

function renderAssistantBlock(call) {
  const wrapper = el('div', { class: 'message assistant' });
  const meta = el('div', { class: 'meta' });
  meta.appendChild(el('span', { class: 'badge', text: 'assistant' }));
  meta.appendChild(el('span', { text: assistantStatusText(call) }));
  wrapper.appendChild(meta);

  const versions = el('div', { class: 'versions' });
  const original = el('div', { class: 'version original' });
  original.textContent = call.originalText || '';
  const modified = (call.finalText || '') !== (call.originalText || '');
  const finalNode = el('div', { class: 'version final' + (modified ? ' modified' : '') });
  finalNode.textContent = call.finalText || call.originalText || '';
  versions.appendChild(original);
  versions.appendChild(finalNode);
  wrapper.appendChild(versions);

  if (!call.completed) {
    wrapper.appendChild(el('div', { class: 'stream-status', text: 'Live updates in progress…' }));
  } else if (call.status === 'failure') {
    wrapper.appendChild(el('div', { class: 'stream-status', text: 'Policy marked this call as failed.' }));
  }
  return wrapper;
}

function renderCall(call, messagePairs) {
  const wrapper = el('div', { class: 'call' });
  const header = el('div', { class: 'call-header' });
  header.appendChild(el('div', { class: 'call-title', text: call.callId }));
  const badge = statusBadge(call);
  header.appendChild(el('span', { class: `badge status-${badge.key}`, text: badge.label }));
  const metaBits = [];
  if (call.startedAt) metaBits.push(`Started ${call.startedAt.toLocaleString()}`);
  if (call.completed && call.finishedAt) metaBits.push(`Finished ${call.finishedAt.toLocaleString()}`);
  if (metaBits.length) {
    header.appendChild(el('div', { class: 'call-meta', text: metaBits.join(' • ') }));
  }
  wrapper.appendChild(header);

  const body = el('div', { class: 'call-body' });

  const requestSection = el('div', { class: 'call-section' });
  requestSection.appendChild(el('div', { class: 'section-title', text: 'Request Messages' }));
  const messagesContainer = el('div', { class: 'call-messages' });
  if (!messagePairs.length) {
    messagesContainer.appendChild(el('div', { class: 'empty-state inline', text: 'No request messages captured.' }));
  } else {
    for (const pair of messagePairs) {
      messagesContainer.appendChild(renderMessagePair(pair));
    }
  }
  requestSection.appendChild(messagesContainer);
  body.appendChild(requestSection);

  const responseSection = el('div', { class: 'call-section' });
  responseSection.appendChild(el('div', { class: 'section-title', text: 'Assistant Output' }));
  responseSection.appendChild(renderAssistantBlock(call));
  body.appendChild(responseSection);

  wrapper.appendChild(body);
  return wrapper;
}

function renderConversation() {
  const container = document.getElementById('chat');
  if (!container) return;
  container.textContent = '';

  const calls = state.callOrder
    .map((callId) => state.calls.get(callId))
    .filter((call) => Boolean(call));
  if (!calls.length) {
    container.appendChild(el('div', { class: 'empty-state', text: 'Select a trace to view the conversation history.' }));
    return;
  }

  const runningContext = [];
  calls.sort((a, b) => {
    const seqA = a.firstSequence ?? Number.MAX_SAFE_INTEGER;
    const seqB = b.firstSequence ?? Number.MAX_SAFE_INTEGER;
    if (seqA !== seqB) return seqA - seqB;
    const timeA = a.startedAt ? a.startedAt.getTime() : Number.MAX_SAFE_INTEGER;
    const timeB = b.startedAt ? b.startedAt.getTime() : Number.MAX_SAFE_INTEGER;
    return timeA - timeB;
  });

  for (const call of calls) {
    const messagePairs = buildMessagePairs(call, runningContext);
    container.appendChild(renderCall(call, messagePairs));

    const updatedContext = cloneMessages(call.request.final);
    if (call.finalText) {
      updatedContext.push({ role: 'assistant', content: call.finalText });
    }
    runningContext.length = 0;
    for (const msg of updatedContext) {
      runningContext.push({ ...msg });
    }
  }
}

function handleRequestStarted(call, payload, timestamp) {
  const originalMessages = normalizeMessages(payload?.original_messages);
  const finalMessagesRaw = normalizeMessages(payload?.final_messages);
  const finalMessages = finalMessagesRaw.length
    ? finalMessagesRaw
    : cloneMessages(originalMessages);
  call.request.original = cloneMessages(originalMessages);
  call.request.final = cloneMessages(finalMessages);
  call.originalText = '';
  call.finalText = '';
  call.originalChunks = [];
  call.finalChunks = [];
  call.completed = false;
  call.status = 'pending';
  call.startedAt = timestamp || call.startedAt;
  call.finishedAt = null;
}

function handleOriginalChunk(call, payload, timestamp) {
  const delta = typeof payload?.delta === 'string' ? payload.delta : '';
  call.originalText += delta;
  call.originalChunks.push({ delta, timestamp });
  if (!call.completed) {
    call.status = 'streaming';
  }
}

function handleFinalChunk(call, payload, timestamp) {
  const delta = typeof payload?.delta === 'string' ? payload.delta : '';
  call.finalText += delta;
  call.finalChunks.push({ delta, timestamp });
  if (!call.completed) {
    call.status = 'streaming';
  }
}

function handleRequestCompleted(call, payload, timestamp) {
  const status = typeof payload?.status === 'string' ? payload.status : 'success';
  call.status = status;
  call.completed = true;
  call.finishedAt = timestamp || call.finishedAt;

  const originalResponse = typeof payload?.original_response === 'string' ? payload.original_response : '';
  const finalResponse = typeof payload?.final_response === 'string' ? payload.final_response : '';

  if (originalResponse) {
    call.originalText = originalResponse;
  } else if (!call.originalText && call.originalChunks.length) {
    call.originalText = call.originalChunks.map((chunk) => chunk.delta || '').join('');
  }

  if (finalResponse) {
    call.finalText = finalResponse;
  } else if (!call.finalText && call.finalChunks.length) {
    call.finalText = call.finalChunks.map((chunk) => chunk.delta || '').join('');
  }

  if (!call.finalText) {
    call.finalText = call.originalText;
  }
}

function consumeEvent(evt, options = {}) {
  if (!evt) return;
  if (state.traceId && evt.trace_id && evt.trace_id !== state.traceId) return;
  const callId = evt.call_id;
  if (!callId) return;
  const call = ensureCall(callId);
  if (!call) return;

  const eventKey = `${evt.event_type || 'unknown'}::${evt.sequence ?? 'none'}::${evt.timestamp ?? 'no-ts'}`;
  if (call.eventKeys.has(eventKey)) {
    if (!options.skipRender) {
      renderConversation();
    }
    return;
  }
  call.eventKeys.add(eventKey);
  call.events.push(evt);
  rebuildCallFromEvents(call);

  if (!options.skipRender) {
    renderConversation();
  }
}

function applySnapshot(snapshot) {
  state.traceId = snapshot?.trace_id || state.traceId;
  state.calls = new Map();
  state.callOrder = [];
  if (Array.isArray(snapshot?.events)) {
    for (const event of snapshot.events) {
      consumeEvent(event, { skipRender: true });
    }
  }
  renderConversation();
}

async function loadConversation(traceId) {
  if (!traceId) return;
  state.traceId = traceId;
  const active = document.getElementById('active-trace');
  if (active) active.value = traceId;
  setStatus('Loading…');
  closeStream();
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation/by_trace?trace_id=${encodeURIComponent(traceId)}`);
    applySnapshot(snapshot);
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
