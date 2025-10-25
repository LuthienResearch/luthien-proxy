2025-02-14 — Tool-call streaming ergonomics plan

## Objective
Make streaming policies—especially tool-call flows—simple to author while keeping the core `LuthienPolicy` contract generic enough for non-judging use cases.

## Guiding requirements
- Preserve the existing `process_request/full_response/streaming_response` hooks so policies remain arbitrarily powerful.
- Provide optional utilities (event layer, tool-call helpers) that policies can opt into without taking on the whole streaming burden.
- Ensure helpers are debuggable: explicit lifecycle events, minimal hidden state, clear error propagation.

## Step-by-step plan
1. **Baseline characterization**
   - Inventory current LiteLLM chunk shapes we receive (content deltas, tool call deltas, finish chunks, errors).
   - Reasoning: We need the real-world permutations before freezing an event API.
   - Pitfalls: Missing edge cases like parallel tool calls or mixed content/tool deltas will force churn later.

2. **Design streaming event schema**
   - Define small dataclasses: `StreamStarted`, `ContentChunk`, `ToolCallDelta`, `ToolCallComplete`, `StreamError`, `StreamClosed`.
   - Reasoning: Typed events give policy authors readable branching while keeping raw chunks reachable.
   - Pitfalls: Over-normalizing (e.g., stripping metadata) would block advanced policies; ensure each event carries `raw_chunk`.

3. **Implement `StreamingEvents.iter_events` helper**
   - Consume an `asyncio.Queue[ModelResponse]`, yield typed events, and surface keepalive hooks.
   - Reasoning: Centralizes chunk parsing, enabling consistent lifecycle handling across policies.
   - Pitfalls: Backpressure—must await downstream puts or cap batch size so we do not buffer unbounded chunks.

4. **Wrap `StreamChunkAggregator`**
   - Create a thin adapter that feeds `ToolCallDelta` events into an aggregator and emits `ToolCallComplete` with a normalized `ToolCall` dataclass.
   - Reasoning: Re-uses proven aggregation logic while hiding its dict-shaped internals.
   - Pitfalls: Ensure partial/incomplete tool calls still expose enough data for fail-safe blocking without surprising authors.

5. **Prototype `ToolCallStreamGate`**
   - Build a helper that ties the event iterator + aggregator together, exposes callbacks (`on_content`, `on_tool_complete`, `on_error`, `on_closed`), and handles buffer forwarding and queue shutdown.
   - Reasoning: Offers the “easy mode” for tool-call policies while remaining optional.
   - Pitfalls: Callback contract must clarify which coroutine controls outgoing flow to avoid double-writes or missed shutdowns.

6. **Refactor `ToolCallJudgePolicy` onto helpers**
   - Replace manual buffer/loop with the new gate, keeping business logic untouched.
   - Reasoning: Validates ergonomics and reveals missing hooks before other policies depend on the API.
   - Pitfalls: Must ensure existing tests keep passing; add coverage for stream rejection + approval paths via the new helpers.

7. **Author exemplar non-judging policy**
   - Implement `CapitalizeEveryNthWordPolicy` using the event helper only (no gate).
   - Reasoning: Demonstrates that helpers work for arbitrary transformations, not just judging.
   - Pitfalls: Need to handle mixed content/tool-call streams; confirm policy forwards non-text events unchanged.

8. **Document usage + migration tips**
   - Update developer docs with short recipes: “writing a streaming policy with events,” “using ToolCallStreamGate,” “handling errors.”
   - Reasoning: Lower barrier for future policies and codify best practices surfaced during refactor.
   - Pitfalls: Docs must warn about stateful helpers needing per-request instantiation to avoid cross-request state bleed.

## Testing strategy
- Unit tests for each helper (event iterator edge cases, aggregator adapter, gate callback contracts).
- Integration-style test that streams a fixture response through both ToolCallJudge and Capitalize policies to confirm chunk ordering, backpressure, and shutdown semantics.

## Open questions
- How do we surface upstream cancellation (client disconnect) to policies—dedicated event or rely on `StreamError`?
- Should helpers accept a configurable batch size for `get_available`, or always process single chunks to minimize latency?

## Risks & mitigations
- **Risk:** Event schema misses LiteLLM variation ⇒ start by collecting real sample logs before implementation.
- **Risk:** Helper hides too much state ⇒ expose simple introspection hooks (e.g., `gate.pending_tool_calls()` for debugging).
- **Risk:** Performance regression in streaming path ⇒ benchmark old vs new loops; optimize only after correctness is locked.
