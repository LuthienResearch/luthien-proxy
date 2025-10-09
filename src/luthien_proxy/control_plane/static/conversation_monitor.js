const SAFE_ATTR_PATTERN = /^[a-zA-Z_][\w:-]*$/;

function sanitizeText(value) {
  if (value == null) return "";
  const text = String(value);
  return text.replace(/[\u2028\u2029]/g, "");
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") {
      node.className = value;
    } else if (key === "text") {
      node.textContent = sanitizeText(value);
    } else if (key === "dataset" && value && typeof value === "object") {
      for (const [dataKey, dataValue] of Object.entries(value)) {
        node.dataset[dataKey] = sanitizeText(dataValue);
      }
    } else {
      if (typeof key === "string" && key.toLowerCase().startsWith("on")) {
        throw new Error(`Event handler attributes are not allowed (saw ${key})`);
      }
      if (typeof key === "string" && !SAFE_ATTR_PATTERN.test(key)) {
        throw new Error(`Attribute name ${key} contains unsupported characters`);
      }
      const safeValue = typeof value === "string" ? sanitizeText(value) : value;
      node.setAttribute(key, safeValue);
    }
  }
  for (const child of children) {
    if (child == null) continue;
    if (typeof child === "string") {
      node.appendChild(document.createTextNode(sanitizeText(child)));
    } else {
      node.appendChild(child);
    }
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
  activeCallId: null,
  traceId: null,
  callsIndex: [],
  calls: new Map(),
  callOrder: [],
  eventSource: null,
  reconnectTimer: null,
  streamPaused: false,
  loading: false,
};

function computeMessagePairs(originalMessages, finalMessages) {
  const originals = Array.isArray(originalMessages) ? originalMessages : [];
  const finals = Array.isArray(finalMessages) && finalMessages.length ? finalMessages : originals;
  const maxLen = Math.max(originals.length, finals.length);
  const pairs = [];
  for (let index = 0; index < maxLen; index += 1) {
    const original = originals[index] || {};
    const final = finals[index] || original;
    pairs.push({
      role: final.role || original.role || "unknown",
      original: typeof original.content === "string" ? original.content : "",
      final:
        typeof final.content === "string"
          ? final.content
          : typeof original.content === "string"
          ? original.content
          : "",
    });
  }
  return pairs;
}

function setStatus(text, mode) {
  const pill = document.getElementById("status");
  if (!pill) return;
  pill.textContent = text;
  pill.classList.remove("live", "error");
  if (mode === "live") pill.classList.add("live");
  if (mode === "error") pill.classList.add("error");
}

function updateActiveCallLabel() {
  const label = document.getElementById("active-call");
  if (!label) return;
  if (!state.activeCallId) {
    label.textContent = "No call selected";
    return;
  }
  if (state.traceId) {
    label.textContent = `Call: ${state.activeCallId} (trace ${state.traceId})`;
  } else {
    label.textContent = `Call: ${state.activeCallId}`;
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

function openStreamForCall(callId) {
  closeStream();
  if (!callId || state.streamPaused) return;
  const targetUrl = state.traceId
    ? `/api/hooks/conversation/stream_by_trace?trace_id=${encodeURIComponent(state.traceId)}`
    : `/api/hooks/conversation/stream?call_id=${encodeURIComponent(callId)}`;
  setStatus("Listening…", "live");
  const source = new EventSource(targetUrl);
  state.eventSource = source;
  source.onmessage = (event) => {
    if (!event.data) return;
    try {
      const payload = JSON.parse(event.data);
      handleEvent(payload, { replay: false });
      setStatus("Live", "live");
    } catch (err) {
      console.error("Failed to parse SSE payload", err);
    }
  };
  source.onerror = () => {
    setStatus("Connection lost; retrying…", "error");
    closeStream();
    state.reconnectTimer = setTimeout(() => {
      if (!state.streamPaused) openStreamForCall(callId);
    }, 2000);
  };
}

function toggleStream() {
  state.streamPaused = !state.streamPaused;
  const toggle = document.getElementById("stream-toggle");
  if (state.streamPaused) {
    closeStream();
    setStatus("Paused");
    if (toggle) toggle.textContent = "Resume Live";
  } else {
    if (toggle) toggle.textContent = "Pause Live";
    if (state.activeCallId) openStreamForCall(state.activeCallId);
  }
}

function ensureCall(callId) {
  if (!callId) return null;
  let call = state.calls.get(callId);
  if (!call) {
    call = {
      callId,
      traceId: null,
      status: "pending",
      startedAt: null,
      completedAt: null,
      requestOriginalMessages: [],
      requestFinalMessages: [],
      requestMessagePairs: [],
      originalChunks: [],
      finalChunks: [],
      originalResponse: "",
      finalResponse: "",
      toolCalls: new Map(),
      toolCallOrder: [],
      snapshotTimer: null,
      toolRefreshTimer: null,
    };
    state.calls.set(callId, call);
  }
  if (!state.callOrder.includes(callId)) {
    state.callOrder.push(callId);
  }
  return call;
}

function adoptCallSnapshot(snapshotCall) {
  const call = ensureCall(snapshotCall.call_id);
  if (!call) return;
  call.traceId = snapshotCall.trace_id || call.traceId;
  call.startedAt = snapshotCall.started_at || call.startedAt;
  call.completedAt = snapshotCall.completed_at || call.completedAt;
  call.status = snapshotCall.status || call.status || "pending";
  call.requestOriginalMessages = Array.isArray(snapshotCall.request_original_messages)
    ? [...snapshotCall.request_original_messages]
    : [];
  call.requestFinalMessages = Array.isArray(snapshotCall.request_final_messages)
    ? [...snapshotCall.request_final_messages]
    : call.requestOriginalMessages;
  call.requestMessagePairs = computeMessagePairs(call.requestOriginalMessages, call.requestFinalMessages);
  call.originalResponse = snapshotCall.original_response || "";
  call.finalResponse = snapshotCall.final_response || call.originalResponse || call.finalResponse || "";
  call.originalChunks = Array.isArray(snapshotCall.original_chunks)
    ? [...snapshotCall.original_chunks]
    : call.originalResponse
    ? [call.originalResponse]
    : call.originalChunks;
  call.finalChunks = Array.isArray(snapshotCall.final_chunks)
    ? [...snapshotCall.final_chunks]
    : call.finalResponse
    ? [call.finalResponse]
    : call.finalChunks;
}

function applySnapshot(snapshot) {
  state.calls.clear();
  state.callOrder = [];
  if (Array.isArray(snapshot.calls)) {
    for (const item of snapshot.calls) {
      adoptCallSnapshot(item);
    }
  }
  if (Array.isArray(snapshot.events)) {
    for (const event of snapshot.events) {
      handleEvent(event, { replay: true });
    }
  }
}

function scheduleSnapshotRefresh(callId, delayMs = 200) {
  const call = state.calls.get(callId);
  if (!call) return;
  if (call.snapshotTimer) {
    clearTimeout(call.snapshotTimer);
  }
  call.snapshotTimer = setTimeout(async () => {
    call.snapshotTimer = null;
    await refreshCallSnapshot(callId);
  }, delayMs);
}

async function refreshCallSnapshot(callId) {
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation?call_id=${encodeURIComponent(callId)}`);
    state.traceId = snapshot.trace_id || state.traceId;
    applySnapshot(snapshot);
    renderTimeline();
    updateActiveCallLabel();
  } catch (err) {
    console.error("Failed to refresh call snapshot", err);
  }
}

function scheduleToolCallRefresh(callId, delayMs = 200) {
  const call = state.calls.get(callId);
  if (!call) return;
  if (call.toolRefreshTimer) {
    clearTimeout(call.toolRefreshTimer);
  }
  call.toolRefreshTimer = setTimeout(async () => {
    call.toolRefreshTimer = null;
    await refreshToolCalls(callId);
  }, delayMs);
}

async function refreshToolCalls(callId) {
  try {
    const records = await fetchJSON(`/api/tool-calls/logs?call_id=${encodeURIComponent(callId)}&limit=50`);
    if (!Array.isArray(records)) return;
    const call = state.calls.get(callId);
    if (!call) return;
    for (const record of records) {
      const entries = Array.isArray(record.tool_calls) ? record.tool_calls : [];
      for (const entry of entries) {
        if (!entry || typeof entry !== "object") continue;
        const id = typeof entry.id === "string" && entry.id ? entry.id : record.stream_id || null;
        if (!id) continue;
        const existing = call.toolCalls.get(id) || {};
        const responseText = normalizeJSONString(entry.response);
        call.toolCalls.set(id, {
          id,
          name: typeof entry.name === "string" ? entry.name : existing.name || "",
          argumentsText: normalizeJSONString(entry.arguments != null ? entry.arguments : existing.argumentsText || ""),
          status: typeof entry.status === "string" && entry.status ? entry.status : existing.status || "pending",
          responseText: responseText || existing.responseText || "",
          raw: entry,
          lastSeen: existing.lastSeen || null,
        });
        if (!call.toolCallOrder.includes(id)) {
          call.toolCallOrder.push(id);
        }
      }
    }
    renderTimeline();
  } catch (err) {
    console.error("Failed to refresh tool call logs", err);
  }
}

function normalizeJSONString(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}

function applyChunk(call, payload, streamKey) {
  const delta = typeof payload.delta === "string" ? payload.delta : "";
  if (!delta) return;
  const chunkIndex = typeof payload.chunk_index === "number" ? payload.chunk_index : null;
  const key = streamKey === "final" ? "finalChunks" : "originalChunks";
  const target = call[key];
  let index = chunkIndex;
  if (index == null || index > target.length) index = target.length;
  if (index === target.length) {
    target.push(delta);
  } else if (target[index] !== delta) {
    target[index] = delta;
  }
  const text = target.join("");
  if (streamKey === "final") {
    call.finalResponse = text;
    if (call.status === "pending") call.status = "streaming";
  } else {
    call.originalResponse = text;
  }
}

function updateToolCallsFromPayload(call, payload, timestamp) {
  const toolCalls = Array.isArray(payload.tool_calls) ? payload.tool_calls : [];
  if (!toolCalls.length) return;
  for (const tc of toolCalls) {
    if (!tc || typeof tc !== "object") continue;
    const id = typeof tc.id === "string" && tc.id ? tc.id : `${call.callId}-tool-${call.toolCallOrder.length}`;
    const func = tc.function && typeof tc.function === "object" ? tc.function : null;
    const name = func && typeof func.name === "string" ? func.name : typeof tc.name === "string" ? tc.name : "";
    const argsRaw =
      func && func.arguments != null
        ? func.arguments
        : tc.arguments != null
        ? tc.arguments
        : tc.input != null
        ? tc.input
        : "";
    const argumentsText = normalizeJSONString(argsRaw);
    const existing = call.toolCalls.get(id) || {};
    call.toolCalls.set(id, {
      id,
      name,
      argumentsText,
      status: existing.status || "pending",
      responseText: existing.responseText || "",
      raw: tc,
      lastSeen: timestamp || existing.lastSeen || null,
    });
    if (!call.toolCallOrder.includes(id)) call.toolCallOrder.push(id);
  }
}

function handleEvent(event, options = {}) {
  if (!event || !event.call_id) return;
  const call = ensureCall(event.call_id);
  if (!call) return;
  if (event.trace_id && !call.traceId) {
    call.traceId = event.trace_id;
    if (state.activeCallId === call.callId) {
      state.traceId = event.trace_id;
      updateActiveCallLabel();
    }
  }

  const timestamp = event.timestamp || null;
  const payload = event.payload || {};

  switch (event.event_type) {
    case "request":
      // New schema: request event with OpenAI format
      call.startedAt = call.startedAt || timestamp;
      call.completedAt = null;
      call.status = "pending";

      // Extract messages from OpenAI format
      const messages = Array.isArray(payload.messages) ? payload.messages : [];
      call.requestMessages = messages;

      // No more original vs final - just store what we have
      call.requestOriginalMessages = messages;
      call.requestFinalMessages = messages;
      call.requestMessagePairs = computeMessagePairs(messages, messages);

      call.responseMessage = null;
      call.originalResponse = "";
      call.finalResponse = "";
      if (!options.replay) scheduleSnapshotRefresh(call.callId, 200);
      break;

    case "response":
      // New schema: response event with OpenAI message format
      const message = payload.message || {};
      const status = payload.status || "success";
      const finishReason = payload.finish_reason;

      call.responseMessage = message;
      call.status = status;
      call.completedAt = timestamp || call.completedAt;

      // Extract text content if available
      if (typeof message.content === "string") {
        call.finalResponse = message.content;
        call.originalResponse = message.content;
        call.finalChunks = [message.content];
      }

      // Handle tool calls if present
      if (Array.isArray(message.tool_calls) && message.tool_calls.length > 0) {
        call.toolCalls = message.tool_calls;
      }

      if (!options.replay) scheduleSnapshotRefresh(call.callId, 200);
      break;

    default:
      break;
  }

  if (!options.replay) renderTimeline();
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

function renderMessageDiff(diff) {
  const container = el("div", { class: "message-card" });
  const header = el("div", { class: "message-header" });
  header.appendChild(el("span", { class: "message-role", text: diff.role || "unknown" }));
  container.appendChild(header);

  // New schema: no original vs final tracking, just show the message
  const content = el("div", { class: "message-content" });
  content.textContent = diff.final || diff.original || "";
  container.appendChild(content);
  return container;
}

function renderAssistantSection(call) {
  const section = el("div", { class: "assistant-card" });
  const statusText =
    call.status === "failure"
      ? "Response failed"
      : call.status === "streaming"
      ? "Streaming response…"
      : "Response complete";
  section.appendChild(el("div", { class: "assistant-status", text: statusText }));
  const header = el("div", { class: "message-header" });
  header.appendChild(el("span", { class: "message-role", text: "assistant" }));
  header.appendChild(el("span", { text: "Original vs Final" }));
  section.appendChild(header);

  const versions = el("div", { class: "message-versions" });
  const original = el("div", { class: "message-version original" });
  original.textContent = call.originalResponse || call.originalChunks.join("") || "";
  versions.appendChild(original);
  const modified = call.finalResponse !== call.originalResponse;
  const finalNode = el("div", { class: "message-version final" + (modified ? " modified" : "") });
  finalNode.textContent = call.finalResponse || call.finalChunks.join("") || original.textContent;
  versions.appendChild(finalNode);
  section.appendChild(versions);
  return section;
}

function renderToolCall(tool) {
  const card = el("div", { class: "tool-call-card" });
  const header = el("div", { class: "tool-call-header" });
  header.appendChild(el("span", { class: "tool-badge", text: "tool" }));
  const titleParts = [];
  if (tool.name) titleParts.push(tool.name);
  if (tool.id) titleParts.push(`#${tool.id}`);
  header.appendChild(el("span", { text: titleParts.join(" ") || "tool call" }));
  card.appendChild(header);
  card.appendChild(el("div", { class: "tool-status", text: `Status: ${tool.status || "pending"}` }));

  if (tool.argumentsText) {
    card.appendChild(el("div", { class: "section-title", text: "Arguments" }));
    const args = el("div", { class: "tool-arguments" });
    args.textContent = tool.argumentsText;
    card.appendChild(args);
  }

  if (tool.responseText) {
    card.appendChild(el("div", { class: "section-title", text: "Response" }));
    const resp = el("div", { class: "tool-response" });
    resp.textContent = tool.responseText;
    card.appendChild(resp);
  }

  return card;
}

function statusBadge(call) {
  switch (call.status) {
    case "success":
    case "stream_summary":
      return { key: "success", label: "Completed" };
    case "failure":
      return { key: "failure", label: "Failed" };
    case "streaming":
      return { key: "streaming", label: "Streaming" };
    default:
      return { key: "pending", label: "Pending" };
  }
}

function renderCall(call) {
  const card = el("div", { class: "call-card" });
  const header = el("div", { class: "call-header" });
  const title = el("div", { class: "call-title" });
  title.appendChild(el("div", { class: "call-title-code", text: call.callId }));
  const metaBits = [];
  const started = formatDate(call.startedAt);
  if (started) metaBits.push(`Started ${started}`);
  const finished = formatDate(call.completedAt);
  if (finished) metaBits.push(`Completed ${finished}`);
  if (metaBits.length) title.appendChild(el("div", { class: "call-meta", text: metaBits.join(" • ") }));
  header.appendChild(title);
  const badge = statusBadge(call);
  header.appendChild(el("span", { class: `badge status-${badge.key}`, text: badge.label }));
  card.appendChild(header);

  const body = el("div", { class: "call-body" });
  const requestSection = el("div", { class: "call-section" });
  requestSection.appendChild(el("div", { class: "section-title", text: "Request Messages" }));
  const requestContainer = el("div", { class: "message-diffs" });
  const pairs = call.requestMessagePairs && call.requestMessagePairs.length ? call.requestMessagePairs : [];
  if (!pairs.length) {
    requestContainer.appendChild(el("div", { class: "empty-state", text: "No request messages recorded." }));
  } else {
    for (const pair of pairs) {
      requestContainer.appendChild(renderMessageDiff(pair));
    }
  }
  requestSection.appendChild(requestContainer);
  body.appendChild(requestSection);

  const responseSection = el("div", { class: "call-section" });
  responseSection.appendChild(el("div", { class: "section-title", text: "Assistant Output" }));
  responseSection.appendChild(renderAssistantSection(call));
  body.appendChild(responseSection);

  if (call.toolCallOrder.length) {
    const toolSection = el("div", { class: "call-section" });
    toolSection.appendChild(el("div", { class: "section-title", text: "Tool Calls" }));
    const list = el("div", { class: "tool-call-list" });
    for (const id of call.toolCallOrder) {
      const tool = call.toolCalls.get(id);
      if (tool) list.appendChild(renderToolCall(tool));
    }
    toolSection.appendChild(list);
    body.appendChild(toolSection);
  }

  card.appendChild(body);

  const footer = el("div", { class: "call-footer" });
  footer.appendChild(el("span", { text: call.traceId ? `Trace: ${call.traceId}` : "" }));
  footer.appendChild(el("span", { text: call.status === "streaming" ? "Streaming" : "" }));
  card.appendChild(footer);
  return card;
}

function renderTimeline() {
  const container = document.getElementById("timeline");
  if (!container) return;
  container.textContent = "";
  if (!state.callOrder.length) {
    container.appendChild(el("div", { class: "empty-state", text: "Select a call to begin monitoring." }));
    return;
  }
  for (const callId of state.callOrder) {
    const call = state.calls.get(callId);
    if (!call) continue;
    container.appendChild(renderCall(call));
  }
}

async function loadRecentCalls() {
  const list = document.getElementById("call-list");
  if (list) list.textContent = "Loading…";
  try {
    const calls = await fetchJSON("/api/hooks/recent_call_ids?limit=50");
    state.callsIndex = Array.isArray(calls) ? calls : [];
    renderCallList();
  } catch (err) {
    console.error("Failed to load recent calls", err);
    if (list) list.textContent = "Failed to load calls";
  }
}

function renderCallList() {
  const list = document.getElementById("call-list");
  if (!list) return;
  list.textContent = "";
  if (!state.callsIndex.length) {
    list.textContent = "No recent calls";
    return;
  }
  for (const entry of state.callsIndex) {
    const item = el("div", {
      class: "trace-item" + (entry.call_id === state.activeCallId ? " active" : ""),
    });
    item.appendChild(el("div", { class: "trace-id", text: entry.call_id }));
    const metaParts = [];
    if (entry.latest) {
      const stamp = formatDate(entry.latest);
      if (stamp) metaParts.push(stamp);
    }
    metaParts.push(`${entry.count} event${entry.count === 1 ? "" : "s"}`);
    item.appendChild(el("div", { class: "trace-meta", text: metaParts.join(" • ") }));
    item.addEventListener("click", () => loadCall(entry.call_id));
    list.appendChild(item);
  }
}

async function hydrateCall(callId) {
  if (!callId) return;
  state.loading = true;
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation?call_id=${encodeURIComponent(callId)}`);
    state.traceId = snapshot.trace_id || null;
    applySnapshot(snapshot);
  } catch (err) {
    console.error("Failed to fetch call snapshot", err);
    setStatus("Failed to load snapshot", "error");
    throw err;
  } finally {
    state.loading = false;
  }
}

async function loadCall(callId) {
  if (!callId || state.loading) return;
  state.activeCallId = callId;
  updateActiveCallLabel();
  renderCallList();
  closeStream();
  setStatus("Loading…");
  try {
    await hydrateCall(callId);
    state.streamPaused = false;
    const toggle = document.getElementById("stream-toggle");
    if (toggle) toggle.textContent = "Pause Live";
    setStatus("Live", "live");
    openStreamForCall(callId);
  } catch (err) {
    setStatus("Failed to load call", "error");
  }
}

function initEventHandlers() {
  const loadBtn = document.getElementById("call-load");
  if (loadBtn) {
    loadBtn.addEventListener("click", () => {
      const input = document.getElementById("call-input");
      const value = input ? input.value.trim() : "";
      if (value) loadCall(value);
    });
  }

  const refreshBtn = document.getElementById("call-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadRecentCalls());
  }

  const reloadBtn = document.getElementById("call-reload");
  if (reloadBtn) {
    reloadBtn.addEventListener("click", () => {
      if (state.activeCallId) refreshCallSnapshot(state.activeCallId);
    });
  }

  const toggleBtn = document.getElementById("stream-toggle");
  if (toggleBtn) toggleBtn.addEventListener("click", toggleStream);

  window.addEventListener("beforeunload", () => closeStream());
}

function init() {
  setStatus("Idle");
  updateActiveCallLabel();
  initEventHandlers();
  loadRecentCalls();
  renderTimeline();
}

document.addEventListener("DOMContentLoaded", init);
