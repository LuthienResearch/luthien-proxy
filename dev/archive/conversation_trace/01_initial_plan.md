# Conversation Trace View Refactor Plan

## Goal
Deliver a conversation-inspection mode that groups events by `litellm_trace_id`, streams live updates without duplication, and keeps the "original" (pre-policy) and "final" (post-policy) token flows distinct.

## Current Data Architecture

### Event Capture
- LiteLLM proxy (`config/litellm_callback.py`) forwards every hook payload to the control plane.
- Each hook write currently produces two rows in `debug_logs`:
  - `hook:{name}` with the raw payload the proxy observed.
  - `hook_result:{name}` with the policy output after control-plane processing.
- Rows carry `litellm_call_id` and (after the latest change) `litellm_trace_id` as top-level fields.

### Conversation Snapshot (`/api/hooks/conversation`)
- Filters `debug_logs` by `litellm_call_id` and hands the rows to `_build_conversation_state`.
- `_build_conversation_state` was designed for a *single request*; it rewrites its internal buffers every time it sees another `async_pre_call_hook` or `async_post_call_success_hook`.

### Conversation-by-trace (`/api/hooks/conversation/by_trace`)
- Filters by `litellm_trace_id` but still uses `_build_conversation_state`, so multiple turns overwrite each other.

### SSE Streams
- `/api/hooks/conversation/stream?call_id=…` publishes the same events we store (request, stream, final) to a per-call Redis channel.
- `/api/hooks/conversation/stream_by_trace?trace_id=…` publishes identical events to the per-trace channel, but the front-end still assumes a single-call state machine.

### Front-End State
- The call view keeps `state.assistant` as a single buffer and works because each page load only shows one request.
- The trace view reuses that code; new responses append to the same buffer instead of allocating a new turn, and "original"/"final" columns get overwritten by later events.

## Pain Points
1. **State clobbering** – `_build_conversation_state` can only represent one request/response pair. Grouping by trace causes message arrays and assistant buffers to be repeatedly overwritten.
2. **Token coupling** – we assume a 1:1 mapping between the raw chunk and the post-policy chunk. Policies can insert or delete tokens arbitrarily, so pairing them is fragile.
3. **UI buffering** – the front-end keeps a single assistant buffer, so previous responses remain when a new turn starts. Live updates appear to "overwrite" the last line because we rewrite the same chunk repeatedly.
4. **Trace stream** – although we publish events per trace, the handler filters by `call_id` and fails to reset state, leading to duplicate/garbled text.

## Design Principles
- Treat `debug_logs` as the **single source of truth** (append-only); derive structured views at runtime rather than maintaining secondary state.
- Publish **decoupled event streams** for the original and final token flows. The UI should not rely on positional pairing.
- Handle **turn boundaries explicitly** (request starts ⇒ reset buffers for that call; final response ⇒ mark turn complete).
- Keep the **snapshot API thin**: return the raw event list (possibly grouped) and let the client render it.

## Target Architecture

### Event Model
Each hook event we expose (database row, SSE message) will have:

| Field | Purpose |
| --- | --- |
| `trace_id` | Conversation-level key (stable across turns). |
| `call_id` | Request-level key (one per turn). |
| `event_type` | `request_started`, `original_chunk`, `final_chunk`, `request_completed`, etc. |
| `sequence` | Monotonic counter or timestamp to preserve order. |
| Payload | JSON matching the event type (e.g. `delta`, `message`). |

We can continue to derive `sequence` from `post_time_ns`; no extra counters required.

### Backend Changes
1. **Event Writing**
   - Keep deep-copying the payload before policy mutation.
   - Emit separate SSE messages for each logical event:
     - `request_started` when `async_pre_call_hook` arrives.
     - `original_chunk` for each raw streaming chunk (`hook:async_post_call_streaming_iterator_hook`).
     - `final_chunk` for each policy chunk (`hook_result:async_post_call_streaming_iterator_hook`).
     - `request_completed` when `async_post_call_success_hook` (or failure) finishes.
   - Publish each event to:
     - Per-call channel (`stream:{call_id}`).
     - Per-trace channel (`stream-trace:{trace_id}`).
     - Optional dashboard channel for "new call/trace" announcements.

2. **Snapshot APIs**
   - `/api/hooks/conversation` → return `{trace_id, call_id, events:[...]}` where `events` is an ordered list of raw log entries (serialized structs). No pre-aggregation.
   - `/api/hooks/conversation/by_trace` → return `{trace_id, call_ids:[...], events:[...]}` with events grouped/ordered across all calls.
   - `/api/hooks/recent_traces` already exists; we’ll reuse it for the sidebar.

3. **Helper cleanup**
   - Remove `_build_conversation_state`; clients will consume the raw event list.
   - Keep `_extract_trace_id`, `_fetch_trace_entries`, `_fetch_trace_entries_by_trace` but make them return normalized event dictionaries.

### Front-End Changes
1. **Local State Model**
   - Maintain a map: `state.calls[call_id] = { original: [], final: [], completed }`.
   - Maintain an ordered array `state.callOrder` (sorted by event time or updated when new calls arrive).
   - `state.traceId` to scope events.

2. **Bootstrap**
   - Fetch `/api/hooks/conversation/by_trace` and replay the returned events to hydrate `state.calls`.
   - Render the conversation by iterating `state.callOrder` and printing each turn’s buffers.

3. **Live Updates**
   - Subscribe to the per-trace SSE stream.
   - On `request_started`, initialize a new entry for `call_id` (and update `callOrder`).
   - On `original_chunk`, append to that call’s `original` buffer.
   - On `final_chunk`, append to the `final` buffer.
   - On `request_completed`, mark `completed = true`.
   - Re-render only the affected call (or append to the DOM incrementally).

4. **Sidebar Updates**
   - Either poll `/api/hooks/recent_traces` periodically or listen to a `new_trace` SSE channel that emits `{trace_id, latest, call_count, event_count}`.

### Implementation Sequence
1. Normalize event writing on the backend—define the new event schema, update SSE publishers, and ensure `_extract_trace_id` is used everywhere.
2. Update the snapshot endpoints to return raw event arrays.
3. Refactor the front-end state to handle multi-turn conversations (trace view first, then reuse the model for the per-call page if desired).
4. Hook up SSE listeners for the new event types and reconcile them with the snapshot data.
5. Remove obsolete helpers/state aggregation code once the new flow works end-to-end.

### Open Questions / Follow-ups
- Should we support incremental resets (policy sends "replace from offset")? For now, `request_started` can implicitly reset the buffers for that call.
- Do we want a single SSE channel that broadcasts both call- and trace-level messages? Keeping them separate keeps filtering simple; we can revisit if we need cross-linking.
- Pagination for historical events? Not required initially, but we might need a limit_+_offset API if conversations get extremely long.

---

With this plan, whoever picks up the implementation can start by reshaping the event producers and snapshot APIs, then rebuild the front-end rendering logic around the new event list. The `debug_logs` schema already has everything necessary; we’re just formalizing the contract and the way we consume it.
