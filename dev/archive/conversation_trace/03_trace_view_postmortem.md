# Conversation Trace View Post-Mortem

## Summary
We successfully reworked the conversation trace experience so that multi-call traces render correctly, original versus final content stays distinct through policy edits, and live streaming no longer duplicates earlier turns. The work touched the backend event normalization, the REST snapshot shape, the SSE pipeline, and both trace and single-call front-end views. The duplication bug that surfaced late in testing was rooted in our front-end replay model; resolving it required a deeper rethink of how we store and rehydrate event state on the client.

## Alignment With the Original Plan
The plan in `dev/conversation_trace_plan.md` called out three main pillars: normalize events server-side, thin the snapshot endpoints, and rebuild the front-end state around a per-call map with explicit turn boundaries. We tracked most of that:

- **Backend event schema**: We introduced `ConversationEvent` objects, published them through both per-call and per-trace channels, and removed `_build_conversation_state` as planned.
- **Snapshot shape**: `/api/hooks/conversation` and `/api/hooks/conversation/by_trace` now return normalized event lists, aligning with the “thin API” goal.
- **Front-end refactor**: We replaced the single-buffer model with a per-call structure, replaying events to hydrate state. The trace view now tracks call ordering explicitly.

Where we diverged:

- **Token pairing simplification**: The plan suggested keeping original/final streams decoupled. In practice we still expose `original_chunk`/`final_chunk` events but rely on the UI to rebuild aggregates; we did not yet move all reconciliation to the backend.
- **New-trace/call announcements**: We stopped short of emitting dedicated SSE announcements for the sidebar; it remains on-demand.
- **Context-aware rendering**: Not in the original plan. We later added the running-context comparison (see below) to eliminate repeated history.

These deviations were pragmatic trade-offs under the time constraint; the core objectives—no duplication, distinct original/final paths, trace-safe state—were met.

## What Happened With the Duplication Bug
### Symptoms
Every new request under a trace caused the UI to repaint all previous turns first, and the "original" column eventually showed policy-mutated text. The DOM dump confirmed that each call showed multiple copies of the same message blocks.

### Root Causes
1. **Incremental mutation**: The front-end watched raw hook events and appended deltas directly to mutable buffers. When a later call triggered the SSE channel to replay earlier events, we re-applied the same updates on top of already-mutated state, duplicating tokens and clobbering the original text.
2. **Conversation history reuse**: The policy echoes prior turns in each request payload. Because we didn't distinguish “context carried forward” from “new messages,” we rendered the entire accumulated conversation for every call.

### Fixes
- **Canonical event list per call**: Each call now stores a deduped array of `ConversationEvent`s. We rebuild derived state (request messages, chunk streams, final text) from scratch on every update. This eliminates accidental mutation and ensures the "original" column reflects the first-seen content.
- **Running context filter**: While rendering calls we keep a clone of the previous call’s final messages. Any request message identical to that context is skipped, so only the new/changed messages for the current call appear. This removed the repeated history blocks the DOM dump showed.

### Why it was hard to pin down
- **State was only reproducible in the browser**: The duplication appeared after a live trace replay; our unit tests only covered backend normalization. Without a deterministic fixture the bug depended on the timing of SSE events and the policy’s habit of resending prior messages.
- **Mutable copies everywhere**: Because we were mutating JSON parsed from the backend, console inspection misled us—objects showed the latest edits even for earlier sequences.
- **No canonical event view**: Lacking a “what does the server think happened?” snapshot meant we were debugging from DOM artifacts instead of inspecting normalized events.

## Suggested Re-Architecture
This episode highlighted design friction. To make future debugging easier:

1. **Normalize events server-side**: Persist canonical “turn” records (request delta, policy edits, final response) in PostgreSQL or Redis keyed by `(trace_id, call_id, sequence)`. Stream those normalized records over SSE instead of raw hook payloads.
2. **Expose a debug inspector**: Serve the canonical event list as JSON so engineers can compare server state to the UI without scraping HTML.
3. **Introduce a strict state machine**: Standardize event types (`turn_start`, `policy_edit`, `turn_complete`) and enforce them both server-side and client-side, reducing fuzzy dedupe logic.
4. **Invest in front-end fixtures**: Add jsdom/Vitest snapshot tests that replay recorded traces. The duplication bug would have shown up instantly with a regression harness.

These changes would keep the browser thin and make data issues diagnosable with SQL or unit tests, not live DOM captures.
