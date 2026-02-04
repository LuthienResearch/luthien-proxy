# Conversation Trace Streaming Post-Mortem

## Overview
We reworked the conversation trace system to expose a debug-friendly, canonical record of every turn while keeping the live UI responsive. The new design emphasizes append-only semantics, explicit chunk indices, and backend-normalized snapshots. This post-mortem records the final architecture, the data flow end to end, key design choices, and discarded approaches.

## Information Flow
### 1. Client Application → Control Plane
- **Entry point:** `src/luthien_proxy/proxy/debug_callback.py` (via `config/litellm_callback.py`).
- **Hooks:** The LiteLLM proxy invokes control-plane hooks (`async_pre_call_hook`, `async_post_call_streaming_iterator_hook`, `async_post_call_success_hook`, `async_post_call_failure_hook`).
- **Fields captured:** Each hook payload includes `litellm_call_id`, `litellm_trace_id`, `post_time_ns`, request messages, streaming deltas, and the raw LLM response.

### 2. Hook Ingestion and Logging
- **Function:** `hook_generic` in `src/luthien_proxy/control_plane/hooks_routes.py`.
- **Logging:** We write two rows per hook into `debug_logs`:
  - `debug_type_identifier = "hook:{hook_name}"` contains the original payload (stored in `jsonblob`).
  - `debug_type_identifier = "hook_result:{hook_name}"` contains the policy result.
- **Fields:** `jsonblob` includes `payload`, `original`, `result`, `litellm_call_id`, `litellm_trace_id`, and `post_time_ns`.

### 3. Event Normalization
- **Helper:** `_build_conversation_events` (same file).
- **Reasoning:** We translate each `hook_result:*` row into a `ConversationEvent` with fields:
  - `call_id`, `trace_id`
  - `event_type` (`request_started`, `original_chunk`, `final_chunk`, `request_completed`)
  - `sequence` derived from `post_time_ns`
  - `payload` containing `chunk_index`, message arrays, deltas, and final responses.
- **Chunk indices:** `_next_chunk_index` assigns monotonic `chunk_index` per stream (`original`, `final`) so downstream consumers can append safely.

### 4. Database Access
- **Queries:** `_fetch_trace_entries` and `_fetch_trace_entries_by_trace` select rows from `debug_logs` filtering by `litellm_call_id` or `litellm_trace_id`.
- **Ordering:** Rows are sorted by `time_created` (and `post_time_ns` when available) to maintain event order.

### 5. Snapshot Assembly
- **Function:** `_build_call_snapshots`.
- **Outputs:** For each call we assemble a `ConversationCallSnapshot` capturing:
  - Metadata (`started_at`, `completed_at`, `status`).
  - `new_messages`: request message diffs against the rolling conversation context.
  - `original_chunks`, `final_chunks`: append-only arrays reconstructed from `chunk_index`.
  - `original_response`, `final_response`: joined strings for convenience.
- **Public API:** `/api/hooks/conversation` and `/api/hooks/conversation/by_trace` now return:
  - Raw `events` (for forensic analysis).
  - `calls`: the canonical snapshots described above.

### 6. Streaming to Web UI
- **Channels:** `_publish_conversation_event` writes JSON events to Redis per-call channels (`luthien:conversation:{call_id}`) and per-trace channels (`luthien:conversation-trace:{trace_id}`).
- **Event content:** Matches `ConversationEvent` schema including `chunk_index` so the browser can incrementally append tokens.

### 7. Web Views
#### Trace View (`conversation_by_trace.js`)
- Hydrates from `/api/hooks/conversation/by_trace`, storing canonical `calls` in state.
- Listens to SSE events; for each `original_chunk`/`final_chunk`, appends `delta` at the specified `chunk_index` (creating new buffers per call) and updates rendered text immediately.
- On `request_started` or `request_completed`, triggers a background snapshot refresh to reconcile diffed messages and metadata.

#### Single-Call View (`conversation_view.js`)
- Similar pattern: hydrate via `/api/hooks/conversation`, append deltas per stream as they arrive, refresh snapshot on completion to capture diffs.

### 8. Tests
- `tests/unit_tests/control_plane/test_hooks_routes.py` verifies event lists, message diffs, chunk arrays, and indices (via `ConversationMessageDiff` and `ConversationCallSnapshot`).

## Design Choices
1. **Append-only buffers per call/stream**
   - Chosen because policies never rewrite sent tokens; each `chunk_index` is monotonic within `original` and `final` streams. Simplifies both server and client logic.

2. **Canonical snapshots on the backend**
   - We assemble `calls` server-side so the UI can hydrate without replaying event logs. This improves debuggability and keeps render logic declarative.

3. **Chunk indices in events**
   - Enables the browser to append tokens incrementally even after falling back to a snapshot. Avoids state drift when SSE events re-deliver historical data.

4. **Message diffs against rolling context**
   - The plan deliberately filters out unchanged history so each call shows only new or modified messages; this matches policy workflows where prior context is echoed back.

## Discarded Approaches
- **Client-side event replay**: earlier versions stored events in JS maps and rebuilt state cumulatively. Debugging duplication/clobbering was painful; we replaced this with backend snapshots plus append-only deltas.
- **Whole-turn snapshot-only updates**: when we used only snapshots, the UI felt “chunky” because it refreshed large blocks. Adding granular chunk events restored responsiveness.
- **Version guards / stream rewrites**: we considered guarding against rewrites, but the policy model guarantees append-only streams, so versioning would add unnecessary complexity.

## Lessons Learned
- Surface canonical state on the backend—it localizes complexity, keeps frontend thin, and gives us natural inspection points.
- Make event contracts explicit. The addition of `chunk_index` turned SSE payloads into idempotent, replayable deltas.
- Always align UI refresh mechanics with the underlying data guarantee. Knowing tokens are append-only let us drop defensive logic and keep the stream smooth.
