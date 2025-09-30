async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

const TRACE_PAGE_LIMIT = 500;
let traceState = null;
let isLoadingMore = false;

async function loadRecentCIDs() {
  const list = document.getElementById("cid-list");
  list.textContent = "Loading…";
  try {
    const items = await fetchJSON("/api/hooks/recent_call_ids?limit=50");
    if (!Array.isArray(items) || items.length === 0) {
      list.textContent = "No recent call IDs";
      return;
    }
    list.textContent = "";
    for (const item of items) {
      const row = el("div", { class: "cid-item" });
      const left = el("div");
      left.appendChild(el("div", { class: "cid", text: item.call_id }));
      left.appendChild(
        el("div", {
          class: "meta",
          text: `${new Date(item.latest).toLocaleString()} • ${item.count} events`,
        }),
      );
      const button = el("button", { class: "pill" }, "Load");
      button.addEventListener("click", () => loadTrace(item.call_id));
      row.append(left, button);
      row.addEventListener("click", (event) => {
        if (event.target !== button) loadTrace(item.call_id);
      });
      list.appendChild(row);
    }
  } catch (error) {
    list.textContent = "Failed to load recent IDs";
  }
}

async function fetchTracePage(callId, offset, limit) {
  const params = new URLSearchParams({
    call_id: callId,
    offset: String(offset),
    limit: String(limit),
  });
  return await fetchJSON(`/api/hooks/trace_by_call_id?${params}`);
}

async function loadTrace(callId) {
  const timeline = document.getElementById("timeline");
  const footer = document.getElementById("timeline-footer");
  const summary = document.getElementById("timeline-summary");
  timeline.textContent = "Loading…";
  footer.textContent = "";
  summary.textContent = "";
  traceState = null;
  try {
    const data = await fetchTracePage(callId, 0, TRACE_PAGE_LIMIT);
    const entries = Array.isArray(data.entries) ? data.entries.slice() : [];
    const limit = typeof data.limit === "number" ? data.limit : TRACE_PAGE_LIMIT;
    const offset = typeof data.offset === "number" ? data.offset : 0;
    const calculatedNext = offset + entries.length;
    traceState = {
      callId,
      entries,
      limit,
      offset,
      hasMore: Boolean(data.has_more),
      nextOffset:
        typeof data.next_offset === "number"
          ? data.next_offset
          : (Number.isFinite(calculatedNext) ? calculatedNext : offset),
    };
    document.getElementById("active-cid").value = callId;
    renderTimeline();
  } catch (error) {
    timeline.textContent = "Failed to load trace";
    footer.textContent = "";
    traceState = null;
  }
}

async function loadMoreTrace() {
  if (!traceState || !traceState.hasMore || isLoadingMore) return;
  isLoadingMore = true;
  const footer = document.getElementById("timeline-footer");
  footer.textContent = "Loading…";
  const offset =
    typeof traceState.nextOffset === "number"
      ? traceState.nextOffset
      : traceState.entries.length;
  try {
    const data = await fetchTracePage(traceState.callId, offset, traceState.limit);
    const newEntries = Array.isArray(data.entries) ? data.entries : [];
    traceState.entries.push(...newEntries);
    traceState.hasMore = Boolean(data.has_more);
    const baseOffset = typeof data.offset === "number" ? data.offset : offset;
    traceState.nextOffset =
      typeof data.next_offset === "number"
        ? data.next_offset
        : baseOffset + newEntries.length;
    renderTimeline();
  } catch (error) {
    footer.textContent = "Failed to load additional entries";
  } finally {
    isLoadingMore = false;
  }
}

function renderTimeline() {
  const container = document.getElementById("timeline");
  const footer = document.getElementById("timeline-footer");
  const summary = document.getElementById("timeline-summary");
  container.textContent = "";
  footer.textContent = "";
  summary.textContent = "";

  if (!traceState) {
    container.textContent = "Select a call ID to load its trace.";
    return;
  }

  const entries = Array.isArray(traceState.entries) ? traceState.entries : [];
  if (entries.length === 0) {
    container.textContent = "No entries for this call_id";
  }

  summary.textContent = `Showing ${entries.length} event${entries.length === 1 ? "" : "s"} (page size ${traceState.limit})`;

  const toNs = (entry) =>
    entry.post_time_ns && typeof entry.post_time_ns === "number"
      ? entry.post_time_ns
      : Date.parse(entry.time) * 1e6;

  let minNs = Infinity;
  for (const entry of entries) {
    const ns = toNs(entry);
    if (!Number.isNaN(ns) && ns < minNs) minNs = ns;
  }

  const formatDelta = (ns) => {
    if (!Number.isFinite(ns)) return "n/a";
    const seconds = Math.max(0, ns) / 1e9;
    return `${seconds.toFixed(3)} s`;
  };

  for (const entry of entries) {
    const ns = toNs(entry);
    const deltaNs = Number.isNaN(ns) || !Number.isFinite(minNs) ? undefined : ns - minNs;
    const ms = ns / 1e6;
    const timestamp = Number.isFinite(ms) ? new Date(ms).toLocaleString() : "";
    const head = el("div", { class: "head" });

    const parts = [];
    const payloadWhen = entry && entry.payload && entry.payload.when;
    if (payloadWhen) parts.push(String(payloadWhen));
    parts.push(`Δt=${formatDelta(deltaNs)}`);
    if (timestamp) parts.push(timestamp);

    let label = "(missing debug_type)";
    if (entry && entry.debug_type) {
      label = entry.debug_type.startsWith("hook:")
        ? entry.debug_type.slice(5)
        : entry.debug_type;
    }

    head.append(
      el("span", { class: "pill" }, label),
      el("span", { class: "src" }, parts.join(" • ")),
    );

    const details = el("details");
    details.append(el("summary", { text: "Details" }));
    const pre = el("pre");
    pre.textContent = JSON.stringify(entry.payload, null, 2);
    details.append(pre);

    const block = el("div", { class: "entry" });
    block.append(head, details);
    container.appendChild(block);
  }

  if (traceState.hasMore) {
    const moreBtn = el("button", { class: "pill" }, `Load more (${traceState.limit})`);
    moreBtn.addEventListener("click", loadMoreTrace);
    footer.appendChild(moreBtn);
  } else if (entries.length) {
    footer.textContent = "End of trace";
  }
}

function init() {
  document.getElementById("cid-load").addEventListener("click", () => {
    const cid = document.getElementById("cid-input").value.trim();
    if (cid) loadTrace(cid);
  });
  document.getElementById("refresh").addEventListener("click", () => {
    const cid = document.getElementById("active-cid").value.trim();
    if (cid) loadTrace(cid);
  });
  loadRecentCIDs();
}

document.addEventListener("DOMContentLoaded", init);
