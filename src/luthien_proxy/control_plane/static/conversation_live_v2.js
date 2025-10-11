// ABOUTME: Enhanced conversation monitor with original vs final tracking
// ABOUTME: Loads DB snapshots and streams Redis updates for live conversation monitoring

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
  const targetUrl = `/api/hooks/conversation/stream?call_id=${encodeURIComponent(callId)}`;
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
      responseOriginal: "",
      responseFinal: "",
      originalChunks: [],
      finalChunks: [],
      snapshotTimer: null,
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

  // Store original and final request messages
  call.requestOriginalMessages = Array.isArray(snapshotCall.request_original_messages)
    ? [...snapshotCall.request_original_messages]
    : [];
  call.requestFinalMessages = Array.isArray(snapshotCall.request_final_messages)
    ? [...snapshotCall.request_final_messages]
    : call.requestOriginalMessages;

  // Store original and final response
  call.responseOriginal = snapshotCall.original_response || "";
  call.responseFinal = snapshotCall.final_response || call.responseOriginal || "";

  // Store chunks for streaming display
  call.originalChunks = Array.isArray(snapshotCall.original_chunks)
    ? [...snapshotCall.original_chunks]
    : call.responseOriginal ? [call.responseOriginal] : [];
  call.finalChunks = Array.isArray(snapshotCall.final_chunks)
    ? [...snapshotCall.final_chunks]
    : call.responseFinal ? [call.responseFinal] : [];
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
  renderTimeline();
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

function extractContent(message) {
  if (typeof message.content === "string") return message.content;
  if (Array.isArray(message.content)) {
    return message.content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && typeof part === "object" && typeof part.text === "string") return part.text;
        return "";
      })
      .join("");
  }
  return "";
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
      call.startedAt = call.startedAt || timestamp;
      call.completedAt = null;
      call.status = "pending";

      // Extract messages from request payload
      const messages = Array.isArray(payload.messages) ? payload.messages : [];

      // For now, treat incoming request as "original" - we'll get final from snapshot
      if (!call.requestOriginalMessages.length) {
        call.requestOriginalMessages = messages;
        call.requestFinalMessages = messages;
      }

      call.responseOriginal = "";
      call.responseFinal = "";
      call.originalChunks = [];
      call.finalChunks = [];

      if (!options.replay) scheduleSnapshotRefresh(call.callId, 200);
      break;

    case "response":
      const message = payload.message || {};
      const status = payload.status || "success";

      call.status = status;
      call.completedAt = timestamp || call.completedAt;

      // Extract text content if available
      const content = extractContent(message);
      if (content) {
        call.responseFinal = content;
        if (!call.responseOriginal) {
          call.responseOriginal = content;
        }
        call.finalChunks = [content];
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

function renderMessageComparison(original, final, role) {
  const container = el("div", { class: "message-comparison" });

  const originalBox = el("div", { class: "message-box original" });
  originalBox.appendChild(el("div", { class: "message-label", text: "Original" }));
  const originalContent = el("div", { class: "message-content" });
  originalContent.textContent = extractContent(original);
  originalBox.appendChild(originalContent);
  container.appendChild(originalBox);

  const finalBox = el("div", { class: "message-box final" });
  finalBox.appendChild(el("div", { class: "message-label", text: "Final" }));
  const finalContent = el("div", { class: "message-content" });
  finalContent.textContent = extractContent(final);

  // Highlight if modified
  const originalText = extractContent(original);
  const finalText = extractContent(final);
  if (originalText !== finalText) {
    finalBox.classList.add("modified");
  }

  finalBox.appendChild(finalContent);
  container.appendChild(finalBox);

  return container;
}

function renderRequestMessages(call) {
  const section = el("div", { class: "section" });
  section.appendChild(el("div", { class: "section-title", text: "Request Messages" }));

  const originals = call.requestOriginalMessages || [];
  const finals = call.requestFinalMessages || [];
  const maxLen = Math.max(originals.length, finals.length);

  if (maxLen === 0) {
    section.appendChild(el("div", { class: "empty-state", text: "No request messages recorded." }));
    return section;
  }

  for (let i = 0; i < maxLen; i++) {
    const original = originals[i] || {};
    const final = finals[i] || original;
    const role = final.role || original.role || "unknown";

    const messageCard = el("div", { class: `message-card ${role}` });
    messageCard.appendChild(el("div", { class: "message-role", text: role }));
    messageCard.appendChild(renderMessageComparison(original, final, role));
    section.appendChild(messageCard);
  }

  return section;
}

function renderResponseComparison(call) {
  const section = el("div", { class: "section" });
  section.appendChild(el("div", { class: "section-title", text: "Response" }));

  const card = el("div", { class: "response-card" });

  const statusText =
    call.status === "failure"
      ? "Failed"
      : call.status === "streaming"
      ? "Streaming…"
      : call.status === "success"
      ? "Complete"
      : "Pending";
  card.appendChild(el("div", { class: `response-status ${call.status}`, text: statusText }));

  const comparison = el("div", { class: "message-comparison" });

  const originalBox = el("div", { class: "message-box original" });
  originalBox.appendChild(el("div", { class: "message-label", text: "Original" }));
  const originalContent = el("div", { class: "message-content" });
  originalContent.textContent = call.responseOriginal || call.originalChunks.join("") || "";
  originalBox.appendChild(originalContent);
  comparison.appendChild(originalBox);

  const finalBox = el("div", { class: "message-box final" });
  finalBox.appendChild(el("div", { class: "message-label", text: "Final" }));
  const finalContent = el("div", { class: "message-content" });
  finalContent.textContent = call.responseFinal || call.finalChunks.join("") || "";

  // Highlight if modified
  if (call.responseOriginal && call.responseFinal && call.responseOriginal !== call.responseFinal) {
    finalBox.classList.add("modified");
  }

  finalBox.appendChild(finalContent);
  comparison.appendChild(finalBox);

  card.appendChild(comparison);
  section.appendChild(card);

  return section;
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
  title.appendChild(el("div", { class: "call-id", text: call.callId }));
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
  body.appendChild(renderRequestMessages(call));
  body.appendChild(renderResponseComparison(call));
  card.appendChild(body);

  const footer = el("div", { class: "call-footer" });
  if (call.traceId) {
    footer.appendChild(el("span", { class: "trace-id", text: `Trace: ${call.traceId}` }));
  }
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
      class: "call-list-item" + (entry.call_id === state.activeCallId ? " active" : ""),
    });
    item.appendChild(el("div", { class: "call-id", text: entry.call_id }));
    const metaParts = [];
    if (entry.latest) {
      const stamp = formatDate(entry.latest);
      if (stamp) metaParts.push(stamp);
    }
    metaParts.push(`${entry.count} event${entry.count === 1 ? "" : "s"}`);
    item.appendChild(el("div", { class: "call-meta", text: metaParts.join(" • ") }));
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
      if (value) {
        loadCall(value);
      }
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
