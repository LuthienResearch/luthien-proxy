# State Management & Observability Refactoring Plan

**Date:** 2025-10-28
**Status:** Planning
**Goal:** Eliminate duplicate state tracking, clarify responsibilities, and separate policy execution from observability

---

## Executive Summary

Our current architecture has **state tracked in multiple places** and **responsibilities mixed across layers**. This refactoring will:

1. Make **StreamState** the single source of truth for stream aggregation
2. Create **TransactionRecorder** to handle all observability/buffering
3. Simplify **ControlPlane** to only execute policies
4. Give **policies access to full stream state** for advanced use cases

---

## Current Architecture & Problems

### Data Flow (Current)

```
┌─────────────────────────────────────────────────────────────┐
│ Gateway (gateway_routes.py)                                 │
│ - HTTP routing + auth                                       │
│ - ❌ LLM calls                                              │
│ - ❌ Format conversion (OpenAI ↔ Anthropic)                │
│ - ❌ Event emission                                         │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         llm_stream (from LiteLLM)
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ SynchronousControlPlane.process_streaming_response()        │
│ - ✅ Create PolicyContext with OTel spans                   │
│ - ✅ Execute policy methods                                 │
│ - ❌ Buffer original chunks (buffering_incoming wrapper)    │
│ - ❌ Emit events to DB/Redis                                │
│ - ❌ Publish real-time updates                              │
│ - ❌ Store _requests dict                                   │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         original_chunks: list[ModelResponse] ❌ BUFFERED HERE
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ StreamingOrchestrator.process()                             │
│ - ✅ Create incoming/outgoing queues                        │
│ - ✅ Launch 3 background tasks (feeder, processor, timeout) │
│ - ✅ Drain outgoing queue                                   │
│ - ❌ Buffer final chunks (if on_complete provided)          │
│ - ❌ Call on_complete callback with buffered chunks         │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         buffered_chunks: list[ModelResponse] ❌ BUFFERED HERE
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ EventBasedPolicy.process_streaming_response()               │
│ - Read from incoming_queue                                  │
│ - Create StreamingChunkAssembler                            │
│ - Write to outgoing_queue                                   │
└────────────────────┬────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ StreamingChunkAssembler.process()                           │
│ - Parse chunks into StreamState                             │
│ - Detect block boundaries                                   │
│ - Call policy hooks with (chunk, state, context)            │
└────────────────────┬────────────────────────────────────────┘
                     ↓
              StreamState (blocks, current, just_completed)
                     ↓
      Policy hooks receive individual blocks
      (NO access to full state, NO raw chunks stored)
```

### State Tracking (Current)

| Location | What's Stored | Purpose | Problem |
|----------|--------------|---------|---------|
| **SynchronousControlPlane.original_chunks** | Pre-policy chunks | Event emission | ❌ Observability in policy executor |
| **StreamingOrchestrator.buffered_chunks** | Post-policy chunks | Event emission via callback | ❌ Observability in orchestrator |
| **StreamState.blocks** | Aggregated blocks | Policy access | ✅ Good! But incomplete |
| **StreamingContext** | Queue + output flag | Output control | ✅ Good! But missing state |
| **PolicyContext.scratchpad** | Per-request policy data | Policy state | ✅ Good! |
| **SynchronousControlPlane._requests** | Request for response processing | Pass to context | ⚠️ Could pass explicitly |

### SRP Violations (Current)

| Component | Should Do | Currently Also Does | Fix |
|-----------|-----------|---------------------|-----|
| **gateway_routes** | HTTP routing + auth | LLM calls, format conversion, events | Extract wrappers |
| **SynchronousControlPlane** | Execute policies | Buffer chunks, emit events, publish to Redis/DB | Extract to recorder |
| **StreamingOrchestrator** | Queue orchestration + timeout | Buffer for observability callback | Remove callback |
| **PolicyContext** | Hold context data | Publish Redis events | Keep OTel, remove Redis |
| **StreamState** | Hold blocks | ❌ Doesn't store raw chunks | Add raw_chunks |
| **StreamingContext** | Output control | ❌ No access to full state | Add StreamState |

---

## Proposed Architecture

### Data Flow (Proposed)

```
┌─────────────────────────────────────────────────────────────┐
│ Gateway (gateway_routes.py)                                 │
│ - HTTP routing + auth                                       │
│ - Create TransactionRecorder                               │
│ - Wrap streams for recording                                │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         llm_stream (from LiteLLM)
                     ↓
         wrapped by recorder.wrap_incoming()
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ TransactionRecorder.wrap_incoming()                        │
│ - Buffer original chunks                                    │
│ - Publish real-time events to Redis                         │
│ - Yield chunks (passthrough)                                │
└────────────────────┬────────────────────────────────────────┘
                     ↓ original_chunks stored in recorder
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ SynchronousControlPlane.process_streaming_response()        │
│ - ✅ Create PolicyContext with OTel spans                   │
│ - ✅ Execute policy methods                                 │
│ - ✅ Handle errors                                          │
│ - ✅ NO buffering                                           │
│ - ✅ NO event emission                                      │
│ - ✅ NO _requests dict (pass explicitly)                    │
└────────────────────┬────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ StreamingOrchestrator.process()                             │
│ - ✅ Create incoming/outgoing queues                        │
│ - ✅ Launch 3 background tasks                              │
│ - ✅ Drain outgoing queue                                   │
│ - ✅ NO buffering                                           │
│ - ✅ NO on_complete callback                                │
└────────────────────┬────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ EventBasedPolicy.process_streaming_response()               │
│ - Read from incoming_queue                                  │
│ - Create StreamingChunkAssembler                            │
│ - Pass StreamState to StreamingContext                      │
│ - Write to outgoing_queue                                   │
└────────────────────┬────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ StreamingChunkAssembler.process()                           │
│ - Parse chunks into StreamState                             │
│ - Store raw chunks in state.raw_chunks                      │
│ - Detect block boundaries                                   │
│ - Call policy hooks with (chunk, state, context)            │
└────────────────────┬────────────────────────────────────────┘
                     ↓
              StreamState (blocks + raw_chunks)
                     ↓
      StreamingContext.state (policies can access full state)
                     ↓
         wrapped by recorder.wrap_outgoing()
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ TransactionRecorder.wrap_outgoing()                        │
│ - Buffer final chunks                                       │
│ - Publish real-time events to Redis                         │
│ - Yield chunks (passthrough)                                │
└────────────────────┬────────────────────────────────────────┘
                     ↓ final_chunks stored in recorder
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ TransactionRecorder.finalize()                             │
│ - Reconstruct original response from original_chunks        │
│ - Reconstruct final response from final_chunks              │
│ - Emit events to DB (historical record)                     │
│ - Emit events to Redis (real-time UI)                       │
└─────────────────────────────────────────────────────────────┘
```

### State Tracking (Proposed)

| Location | What's Stored | Purpose | Change |
|----------|--------------|---------|--------|
| **StreamState.blocks** | Aggregated blocks | Policy access to blocks | ✅ Keep |
| **StreamState.raw_chunks** | Raw chunks from LLM | Reconstruction, debugging | ➕ NEW |
| **StreamingContext.state** | Reference to StreamState | Policy access to full state | ➕ NEW |
| **TransactionRecorder.original_chunks** | Pre-policy chunks | Observability | ➕ NEW |
| **TransactionRecorder.final_chunks** | Post-policy chunks | Observability | ➕ NEW |
| **PolicyContext.scratchpad** | Per-request policy data | Policy state | ✅ Keep |
| ~~SynchronousControlPlane.original_chunks~~ | ❌ DELETED | | ➖ REMOVED |
| ~~StreamingOrchestrator.buffered_chunks~~ | ❌ DELETED | | ➖ REMOVED |
| ~~SynchronousControlPlane._requests~~ | ❌ DELETED | | ➖ REMOVED |

---

## Refactoring Tasks

### Phase 1: Low-Risk Enhancements (✅ Can do today)

#### Task 1: ✅ Rename StreamProcessor → StreamingChunkAssembler (DONE!)

**Status:** COMPLETED
**Why:** Clarify that it assembles chunks into blocks

#### Task 2: Remove `on_complete` from StreamingOrchestrator

**Files:** `streaming_orchestrator.py`, `synchronous_control_plane.py`
**Lines:** ~30 deleted
**Risk:** Low

**Changes:**
- Remove `on_complete` parameter from `StreamingOrchestrator.process()`
- Remove `buffered_chunks: list[T] = []` initialization
- Remove `if on_complete: buffered_chunks.append(chunk)` buffering
- Remove `await on_complete(buffered_chunks)` callback invocation
- Update `SynchronousControlPlane` to not pass `on_complete=emit_streaming_events`

**Before:**
```python
async def process(
    self,
    incoming_stream: AsyncIterator[T],
    processor: ...,
    timeout_seconds: float = 30.0,
    span: Span | None = None,
    on_complete: Callable[[list[T]], Coroutine] | None = None,  # ❌ DELETE
) -> AsyncIterator[T]:
    buffered_chunks: list[T] = [] if on_complete else []  # ❌ DELETE
    ...
    if on_complete:  # ❌ DELETE
        buffered_chunks.append(chunk)
    ...
    await on_complete(buffered_chunks)  # ❌ DELETE
```

**After:**
```python
async def process(
    self,
    incoming_stream: AsyncIterator[T],
    processor: ...,
    timeout_seconds: float = 30.0,
    span: Span | None = None,
) -> AsyncIterator[T]:
    # Just drain and yield - no buffering!
    ...
```

#### Task 3: Add `raw_chunks` to StreamState

**Files:** `stream_state.py`, `streaming_chunk_assembler.py`
**Lines:** ~30 added
**Risk:** Low (additive change)

**Changes:**
- Add `raw_chunks: list[ModelResponse]` field to StreamState
- Add helper methods for querying state
- Update StreamingChunkAssembler to store chunks in `state.raw_chunks`

**Code:**
```python
@dataclass
class StreamState:
    """Complete state of a streaming response."""

    blocks: list[StreamBlock] = field(default_factory=list)
    current_block: StreamBlock | None = None
    just_completed: StreamBlock | None = None
    finish_reason: str | None = None

    # NEW: Store raw chunks for reconstruction/debugging
    raw_chunks: list[ModelResponse] = field(default_factory=list)

    # NEW: Helper methods
    def get_all_content(self) -> str:
        """Get all content text accumulated so far."""
        return "".join(
            b.content for b in self.blocks
            if isinstance(b, ContentStreamBlock)
        )

    def get_completed_tool_calls(self) -> list[ToolCallStreamBlock]:
        """Get all completed tool call blocks."""
        return [
            b for b in self.blocks
            if isinstance(b, ToolCallStreamBlock) and b.is_complete
        ]

    def to_response_dict(self) -> dict:
        """Reconstruct complete response from chunks."""
        from luthien_proxy.v2.storage.events import reconstruct_full_response_from_chunks
        return reconstruct_full_response_from_chunks(self.raw_chunks)
```

**In StreamingChunkAssembler:**
```python
async def process(self, incoming: AsyncIterator[ModelResponse], context: Any) -> None:
    async for chunk in incoming:
        self.state.raw_chunks.append(chunk)  # NEW!
        self._update_state(chunk)
        # ... rest unchanged
```

#### Task 4: Add StreamState to StreamingContext

**Files:** `event_based_policy.py`
**Lines:** ~15 changed
**Risk:** Low (additive change)
**Dependencies:** Task 3

**Changes:**
- Add `state: StreamState` parameter to StreamingContext.__init__()
- Pass StreamState when creating StreamingContext in EventBasedPolicy
- Update docstrings

**Code:**
```python
class StreamingContext:
    """Per-request context for streaming policy hooks."""

    def __init__(
        self,
        policy_context: PolicyContext,
        stream_state: StreamState,  # NEW!
        keepalive: Callable[[], None] | None,
        outgoing: asyncio.Queue[ModelResponse],
    ):
        self.policy_context = policy_context
        self.state = stream_state  # NEW! Policies can access full state
        self.keepalive = keepalive
        self._outgoing = outgoing
        self._output_finished = False
```

**Usage in EventBasedPolicy:**
```python
# Create streaming context
streaming_ctx = StreamingContext(
    policy_context=context,
    stream_state=processor.state,  # NEW! Pass the state
    keepalive=keepalive,
    outgoing=outgoing_queue,
)
```

**Policy Example:**
```python
class StopAfter3ToolCallsPolicy(EventBasedPolicy):
    async def on_tool_call_complete(self, block, context, streaming_ctx):
        # NEW: Can access full state!
        completed_tools = streaming_ctx.state.get_completed_tool_calls()
        if len(completed_tools) >= 3:
            streaming_ctx.mark_output_finished()
            context.emit("policy.max_tools_reached", "Stopped after 3 tool calls")
```

### Phase 2: Observability Refactor (Bigger change)

#### Task 5: Create TransactionRecorder

**Files:** NEW `v2/observability/conversation_recorder.py`
**Lines:** ~150-200 new
**Risk:** Medium (new abstraction)
**Dependencies:** Tasks 2-4

**Design Question:** Should TransactionRecorder be:

**Option A: Active object (stores + emits)**
```python
class TransactionRecorder:
    def __init__(self, call_id, db_pool, event_publisher):
        self.call_id = call_id
        self.original_chunks: list[ModelResponse] = []
        self.final_chunks: list[ModelResponse] = []
        # ...

    async def wrap_incoming(self, stream: AsyncIterator) -> AsyncIterator:
        async for chunk in stream:
            self.original_chunks.append(chunk)
            # publish real-time event
            yield chunk

    async def wrap_outgoing(self, stream: AsyncIterator) -> AsyncIterator:
        async for chunk in stream:
            self.final_chunks.append(chunk)
            # publish real-time event
            yield chunk

    async def finalize(self):
        # Emit to DB/Redis
        ...
```

**Option B: Simple wrappers (functional style)**
```python
# Just functions in gateway_routes.py
async def record_incoming(stream, recorder_fn):
    async for chunk in stream:
        recorder_fn(chunk)
        yield chunk

async def record_outgoing(stream, recorder_fn):
    async for chunk in stream:
        recorder_fn(chunk)
        yield chunk
```

**Recommendation:** Option A - clearer ownership of buffered data

#### Task 6: Simplify SynchronousControlPlane

**Files:** `synchronous_control_plane.py`
**Lines:** ~100-150 deleted/changed
**Risk:** Medium (changes existing code)
**Dependencies:** Task 5

**Changes:**
- Remove `buffering_incoming()` wrapper (~30 lines)
- Remove `emit_streaming_events()` callback (~60 lines)
- Remove `self._requests` dict and storage logic (~20 lines)
- Update method signatures to take `request` parameter explicitly
- Remove `db_pool`, `redis_conn` parameters
- Simplify to just: create context, execute policy, handle errors

**Before:**
```python
class SynchronousControlPlane:
    def __init__(self, policy, event_publisher):
        self.policy = policy
        self.event_publisher = event_publisher
        self._requests: dict[str, Request] = {}  # ❌ DELETE
        self.streaming_orchestrator = StreamingOrchestrator()

    async def process_streaming_response(
        self,
        incoming: AsyncIterator[ModelResponse],
        call_id: str,
        db_pool: db.DatabasePool | None = None,  # ❌ DELETE
        redis_conn: Any | None = None,           # ❌ DELETE
    ):
        # ❌ Buffer original chunks
        original_chunks: list[ModelResponse] = []
        async def buffering_incoming():
            async for chunk in incoming:
                original_chunks.append(chunk)
                # publish events...
                yield chunk

        # ❌ Emit events callback
        async def emit_streaming_events(final_chunks):
            # Reconstruct responses, emit to DB/Redis...
            ...

        # Get stored request
        request = self._requests.get(call_id)  # ❌ DELETE

        async for chunk in self.streaming_orchestrator.process(
            buffering_incoming(),
            policy_processor,
            on_complete=emit_streaming_events,  # ❌ DELETE
        ):
            yield chunk
```

**After:**
```python
class SynchronousControlPlane:
    def __init__(self, policy):
        self.policy = policy
        self.streaming_orchestrator = StreamingOrchestrator()

    async def process_streaming_response(
        self,
        incoming: AsyncIterator[ModelResponse],
        call_id: str,
        request: Request,  # NEW! Explicit parameter
    ):
        # Just execute policy - no buffering, no events!
        with tracer.start_as_current_span("control_plane.process_streaming_response") as span:
            context = PolicyContext(call_id=call_id, span=span, request=request)

            async def policy_processor(incoming_queue, outgoing_queue, keepalive):
                await self.policy.process_streaming_response(
                    incoming_queue, outgoing_queue, context, keepalive=keepalive
                )

            async for chunk in self.streaming_orchestrator.process(
                incoming,
                policy_processor,
            ):
                yield chunk
```

#### Task 7: Simplify PolicyContext (Optional)

**Files:** `policy_context.py`
**Lines:** ~30 changed
**Risk:** Low
**Dependencies:** Task 5

**Question:** Should PolicyContext still emit events?

**Option A: Keep emit() but only for OTel**
```python
def emit(self, event_type, summary, details, severity="info"):
    # Only emit to OTel span
    self.span.add_event(event_type, attributes=attributes)
    # NO Redis publishing
```

**Option B: Remove emit() entirely**
```python
# Policies just use span directly
context.span.add_event("my_event", attributes={...})
```

**Option C: Keep emit() with Redis (no change)**
- Argument: Policies should be able to emit real-time events
- Counter: That's observability, should go through recorder

**Recommendation:** Option A - keep emit() for OTel, remove Redis

### Phase 3: Gateway Cleanup (Future work)

#### Task 8: Extract Service Layer or Add Wrappers

**Files:** `gateway_routes.py`, NEW `v2/service.py`?
**Lines:** ~200+ changed
**Risk:** High (significant refactor)
**Dependencies:** All above

**Two approaches:**

**Approach A: Service Layer**
- Create `ConversationService` class
- Orchestrates: recorder + control plane + format conversion
- Gateway just calls service

**Approach B: Wrapper Pattern**
- Keep gateway mostly as-is
- Add recorder wrappers inline
- Simpler, less abstraction

**Recommendation:** Approach B for now - defer service layer extraction

---

## Outstanding Questions

### 1. TransactionRecorder Design

**Q:** Should it be an active object (class with state) or functional wrappers?
**A:** Leaning toward active object for clear ownership

**Q:** Where should it live?
**Options:**
- `v2/observability/conversation_recorder.py` (new file)
- Just inline in `gateway_routes.py` (simpler)

**Recommendation:** Start inline, extract later if needed

### 2. Event Emission from Policies

**Q:** Should policies be able to emit real-time events (Redis)?
**Options:**
- Yes - policies know important context
- No - that's observability, not policy logic

**Current:** Policies call `context.emit()` which publishes to Redis
**Proposal:** Keep emit() but only for OTel, remove Redis publishing

### 3. Request Storage Pattern

**Q:** Should we pass request explicitly or store somewhere?
**Current:** `_requests` dict in ControlPlane
**Proposal:** Pass explicitly as parameter

**Trade-offs:**
- Pass explicitly: More parameters, but clearer
- Store in dict: Fewer parameters, but stateful

**Recommendation:** Pass explicitly - clarity over convenience

### 4. Format Conversion Location

**Q:** Where should OpenAI ↔ Anthropic conversion happen?
**Current:** In `gateway_routes.py` streaming loop
**Options:**
- Keep in gateway (current)
- Move to service layer (future)
- Separate middleware?

**Recommendation:** Leave as-is for now

### 5. Non-Streaming Path

**Q:** Should we refactor non-streaming too?
**Current:** Similar issues but simpler (no queues)
**Proposal:** Apply same pattern but lower priority

**Recommendation:** Do streaming first, then non-streaming

---

## Testing Strategy

### Phase 1 Testing
- Unit tests for StreamState helper methods
- Unit tests for StreamingContext with state access
- Integration tests: policies accessing full state
- Verify no regression in existing tests

### Phase 2 Testing
- Unit tests for TransactionRecorder wrappers
- Integration tests: verify events still emitted correctly
- E2E tests: full request-response with recording
- Verify DB/Redis events match before/after

### Phase 3 Testing
- E2E tests for full flow
- Performance tests (check memory usage with buffering)
- Observability validation (check UI shows correct data)

---

## Success Metrics

### Code Quality
- [ ] StreamState is single source of truth for stream data
- [ ] No duplicate buffering across components
- [ ] ControlPlane only executes policies (no observability)
- [ ] Clear separation: execution vs observability

### Functionality
- [ ] All existing tests pass
- [ ] Policies can access full stream state
- [ ] Events still emitted correctly to DB/Redis
- [ ] Real-time UI still works

### Developer Experience
- [ ] Easier to write policies (access to full state)
- [ ] Clearer component responsibilities
- [ ] Better naming (StreamingChunkAssembler vs StreamProcessor)
- [ ] Less cognitive load when reading code

---

## Rollout Plan

### Week 1: Phase 1 (Low Risk)
- Day 1: Task 2 (remove on_complete)
- Day 2: Task 3 (add raw_chunks to StreamState)
- Day 3: Task 4 (add StreamState to StreamingContext)
- Day 4: Testing and validation
- Day 5: Document new policy capabilities

### Week 2: Phase 2 (Observability)
- Day 1-2: Task 5 (create TransactionRecorder)
- Day 3-4: Task 6 (simplify ControlPlane)
- Day 5: Task 7 (simplify PolicyContext) + testing

### Week 3: Phase 3 (Gateway) - Optional
- Evaluate if needed
- Could defer to future sprint

---

## Rollback Plan

Each phase is independent and additive:

**Phase 1:** New capabilities, minimal changes to existing code
- If problems: Just don't use new features yet
- Low rollback risk

**Phase 2:** Moves observability logic
- If problems: Can revert TransactionRecorder changes
- Keep Phase 1 enhancements
- Medium rollback risk

**Phase 3:** Gateway refactor
- If problems: Revert gateway changes
- Keep Phases 1 & 2
- High rollback risk (defer until confident)

---

## Related Documents

- `dev/context/codebase_learnings.md` - Architecture insights
- `dev/context/decisions.md` - Past technical decisions
- `dev/observability-v2.md` - Observability architecture

---

## Change Log

- 2025-10-28: Initial plan created
- 2025-10-28: Task 1 completed (rename StreamProcessor)
