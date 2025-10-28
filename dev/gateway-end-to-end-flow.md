# Gateway End-to-End Flow: Complete Design

**Date:** 2025-10-28
**Purpose:** Map complete request-to-response flow showing how we achieve all design goals

---

## Design Goals

1. ✅ **Generate transaction ID** and record original request
2. ✅ **Apply policy to request** (convert to common format first)
3. ✅ **Policy decides**: make backend request OR return immediate response
4. ✅ **Record sent request** (if backend called) OR **record immediate response** (if policy short-circuits)
5. ✅ **Record original response** from backend (if received)
6. ✅ **Apply policy to response** with access to full stream state
   - For streaming: called on each chunk, block complete, stream complete
   - Policy sees all partial/complete blocks
7. ✅ **Record final response** and send to client
8. ✅ **Incoming/outgoing streams fully decoupled** - policy can send 0, 1, or N outputs per input

---

## Complete Flow Diagram

```
┌───────────────────────────────────────────────────────────────────┐
│ 1. CLIENT REQUEST ARRIVES                                         │
│    POST /v1/chat/completions (OpenAI format)                      │
│        OR                                                          │
│    POST /v1/messages (Anthropic format)                           │
│    Headers: Authorization, Content-Type                           │
│    Body: {model, messages, stream: true, ...}                     │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 2. GATEWAY LAYER (gateway_routes.py)                              │
│                                                                    │
│    a. Verify auth token                                           │
│    b. Generate transaction_id = uuid4()                           │
│    c. Parse request body                                          │
│    d. FORMAT CONVERSION (if needed):                              │
│       IF endpoint is /v1/messages (Anthropic):                    │
│           request_data = anthropic_to_openai_request(body)        │
│       ELSE:                                                        │
│           request_data = body  # Already OpenAI format            │
│       → Policies always see OpenAI format (common internal)       │
│                                                                    │
│    e. Create Request object from request_data                     │
│    f. Create TransactionRecorder(transaction_id)                  │
│    g. Record original request:                                    │
│       recorder.record_original_request(request)                   │
│       → DB: conversations table                                   │
│       → Redis: real-time event                                    │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 3. POLICY PROCESSING - REQUEST                                    │
│    ConversationHandler.process_request()                          │
│    *** Policy sees OpenAI format (normalized) ***                 │
│                                                                    │
│    a. Create PolicyContext:                                       │
│       - transaction_id                                            │
│       - OTel span                                                 │
│       - request                                                   │
│       - scratchpad (for policy state)                             │
│                                                                    │
│    b. Call policy.process_request(request, context)               │
│       → Policy can:                                               │
│          - Modify request                                         │
│          - Raise exception (reject)                               │
│          - Return PolicyDecision(action, modified_request)        │
│                                                                    │
│    c. Handle policy decision:                                     │
│       IF action == "send_to_backend":                             │
│          final_request = modified_request                         │
│          recorder.record_final_request(final_request)             │
│          → Proceed to backend call (step 4)                       │
│                                                                    │
│       ELIF action == "immediate_response":                        │
│          immediate_response = modified_request.response           │
│          recorder.record_immediate_response(immediate_response)   │
│          → Skip to step 7 (send to client)                        │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 4. BACKEND CALL (if policy approved)                              │
│    LiteLLM Integration                                            │
│                                                                    │
│    a. Call LiteLLM with final_request:                            │
│       llm_stream = await litellm.acompletion(                     │
│           model=final_request.model,                              │
│           messages=final_request.messages,                        │
│           stream=True,                                            │
│           ...                                                     │
│       )                                                           │
│                                                                    │
│    b. Wrap stream for recording:                                  │
│       wrapped_stream = recorder.wrap_incoming(llm_stream)         │
│       → Records each chunk as it arrives                          │
│       → Publishes real-time events to Redis                       │
│       → Yields chunks (passthrough)                               │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
                  LLM chunks flowing
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 5. RECORDER - INCOMING STREAM                                     │
│    TransactionRecorder.wrap_incoming()                            │
│                                                                    │
│    async for chunk in llm_stream:                                 │
│        # Record                                                   │
│        self.original_chunks.append(chunk)                         │
│                                                                    │
│        # Publish real-time event                                  │
│        await event_publisher.publish_event(                       │
│            transaction_id=transaction_id,                         │
│            event_type="streaming.chunk_received",                 │
│            data={"chunk_index": len(original_chunks), ...}        │
│        )                                                          │
│                                                                    │
│        # Passthrough                                              │
│        yield chunk                                                │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 6. STREAMING ORCHESTRATOR                                         │
│    StreamingOrchestrator.process()                                │
│                                                                    │
│    Purpose: Bridge async iterator ↔ queue-based processing        │
│                                                                    │
│    a. Create queues:                                              │
│       incoming_queue = asyncio.Queue()                            │
│       outgoing_queue = asyncio.Queue()                            │
│                                                                    │
│    b. Launch 3 background tasks:                                  │
│       Task 1: Feed incoming stream → incoming_queue               │
│       Task 2: Run policy_processor(incoming_queue, outgoing_queue)│
│       Task 3: Monitor timeout (raise if no activity)              │
│                                                                    │
│    c. Drain outgoing_queue:                                       │
│       while True:                                                 │
│           chunk = await outgoing_queue.get()                      │
│           if chunk is None: break                                 │
│           yield chunk                                             │
│                                                                    │
│    Note: NO buffering here! Just pure queue orchestration.        │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
                  incoming_queue
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 7. POLICY PROCESSING - STREAMING RESPONSE                         │
│    EventBasedPolicy.process_streaming_response()                  │
│                                                                    │
│    a. Create StreamingContext:                                    │
│       streaming_ctx = StreamingContext(                           │
│           policy_context=context,                                 │
│           stream_state=<will be created>,                         │
│           outgoing=outgoing_queue,                                │
│           keepalive=keepalive_fn,                                 │
│       )                                                           │
│                                                                    │
│    b. Call on_stream_start(context, streaming_ctx)                │
│                                                                    │
│    c. Create StreamingChunkAssembler with callback:               │
│       assembler = StreamingChunkAssembler(                        │
│           on_chunk_callback=self._dispatch_to_hooks               │
│       )                                                           │
│                                                                    │
│    d. Process incoming queue through assembler:                   │
│       await assembler.process(                                    │
│           queue_to_iter(incoming_queue),                          │
│           streaming_ctx                                           │
│       )                                                           │
│                                                                    │
│    e. Call on_stream_complete(stream_state, context)              │
│                                                                    │
│    f. Shutdown outgoing queue (signal end)                        │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 8. STREAMING CHUNK ASSEMBLER                                      │
│    StreamingChunkAssembler.process()                              │
│                                                                    │
│    Purpose: Parse raw chunks into structured blocks               │
│                                                                    │
│    async for chunk in incoming:                                   │
│        # Store raw chunk                                          │
│        self.state.raw_chunks.append(chunk)                        │
│                                                                    │
│        # Parse and update state                                   │
│        self._update_state(chunk)                                  │
│        → Detects block boundaries                                 │
│        → Aggregates deltas into blocks                            │
│        → Sets just_completed when block finishes                  │
│                                                                    │
│        # Call policy callback                                     │
│        await on_chunk_callback(chunk, self.state, streaming_ctx)  │
│                                                                    │
│        # Clear just_completed signal                              │
│        self.state.just_completed = None                           │
│                                                                    │
│    State contains:                                                │
│    - blocks: [ContentStreamBlock, ToolCallStreamBlock, ...]       │
│    - current_block: ToolCallStreamBlock (in-progress)             │
│    - just_completed: ContentStreamBlock (just finished)           │
│    - finish_reason: None (or "stop", "tool_calls", etc.)          │
│    - raw_chunks: [ModelResponse, ModelResponse, ...]              │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 9. POLICY HOOKS DISPATCHING                                       │
│    EventBasedPolicy._dispatch_to_hooks()                          │
│                                                                    │
│    Called by assembler on each chunk with updated state.          │
│                                                                    │
│    # Content delta?                                               │
│    if state.current_block is ContentStreamBlock:                  │
│        delta = extract_content_delta(chunk)                       │
│        if delta:                                                  │
│            await self.on_content_delta(                           │
│                delta=delta,                                       │
│                stream_state=state,                                │
│                context=context,                                   │
│                streaming_ctx=streaming_ctx,                       │
│            )                                                      │
│                                                                    │
│    # Content complete?                                            │
│    if state.just_completed is ContentStreamBlock:                 │
│        await self.on_content_complete(                            │
│            stream_state=state,                                    │
│            context=context,                                       │
│            streaming_ctx=streaming_ctx,                           │
│        )                                                          │
│                                                                    │
│    # Tool call delta?                                             │
│    if state.current_block is ToolCallStreamBlock:                 │
│        await self.on_tool_call_delta(                             │
│            chunk=chunk,                                           │
│            stream_state=state,                                    │
│            context=context,                                       │
│            streaming_ctx=streaming_ctx,                           │
│        )                                                          │
│                                                                    │
│    # Tool call complete?                                          │
│    if state.just_completed is ToolCallStreamBlock:                │
│        await self.on_tool_call_complete(                          │
│            stream_state=state,                                    │
│            context=context,                                       │
│            streaming_ctx=streaming_ctx,                           │
│        )                                                          │
│                                                                    │
│    # Finish reason?                                               │
│    if state.finish_reason:                                        │
│        await self.on_finish_reason(                               │
│            finish_reason=state.finish_reason,                     │
│            stream_state=state,                                    │
│            context=context,                                       │
│            streaming_ctx=streaming_ctx,                           │
│        )                                                          │
│                                                                    │
│    Policy hooks have FULL ACCESS to:                              │
│    - stream_state.blocks (all blocks)                             │
│    - stream_state.current_block (in-progress)                     │
│    - stream_state.just_completed (just finished)                  │
│    - stream_state.raw_chunks (all raw chunks)                     │
│    - stream_state.get_all_content() (helper)                      │
│    - stream_state.get_completed_tool_calls() (helper)             │
│                                                                    │
│    Policy hooks push to outgoing via streaming_ctx:               │
│    - streaming_ctx.send(chunk)                                    │
│    - streaming_ctx.send_text(text)                                │
│    - streaming_ctx.mark_output_finished()                         │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
                  outgoing_queue
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 10. RECORDER - OUTGOING STREAM                                    │
│     TransactionRecorder.wrap_outgoing()                           │
│                                                                    │
│     async for chunk in orchestrator.process(...):                 │
│         # Record                                                  │
│         self.final_chunks.append(chunk)                           │
│                                                                    │
│         # Publish real-time event                                 │
│         await event_publisher.publish_event(                      │
│             transaction_id=transaction_id,                        │
│             event_type="streaming.chunk_sent",                    │
│             data={"chunk_index": len(final_chunks), ...}          │
│         )                                                         │
│                                                                    │
│         # Passthrough                                             │
│         yield chunk                                               │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 11. FORMAT CONVERSION (OUTPUT)                                    │
│     Convert from internal (OpenAI) to client format               │
│                                                                    │
│     *** Policy output is always OpenAI format ***                 │
│     *** Convert to client's expected format here ***              │
│                                                                    │
│     IF client requested /v1/messages (Anthropic):                 │
│         # Need to convert OpenAI → Anthropic                      │
│         converted_chunk = openai_chunk_to_anthropic_chunk(chunk)  │
│         yield converted_chunk                                     │
│     ELSE:                                                         │
│         # Client expects OpenAI, no conversion needed             │
│         yield chunk                                               │
│                                                                    │
│     Note: This happens AFTER policy (step 10) but BEFORE client   │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 12. SEND TO CLIENT                                                │
│     Gateway yields SSE chunks in client's expected format         │
│                                                                    │
│     async for chunk in converted_stream:                          │
│         yield f"data: {json.dumps(chunk)}\n\n"                    │
│                                                                    │
│     Client receives:                                              │
│     - OpenAI format if /v1/chat/completions                       │
│     - Anthropic format if /v1/messages                            │
│                                                                    │
└────────────────────────┬──────────────────────────────────────────┘
                         ↓
┌───────────────────────────────────────────────────────────────────┐
│ 13. FINALIZE RECORDING                                            │
│     TransactionRecorder.finalize()                                │
│                                                                    │
│     a. Reconstruct responses:                                     │
│        original_response = reconstruct_from_chunks(               │
│            self.original_chunks                                   │
│        )                                                          │
│        final_response = reconstruct_from_chunks(                  │
│            self.final_chunks                                      │
│        )                                                          │
│                                                                    │
│     b. Emit to database:                                          │
│        emit_response_event(                                       │
│            transaction_id=transaction_id,                         │
│            original_response=original_response,                   │
│            final_response=final_response,                         │
│            db_pool=db_pool,                                       │
│        )                                                          │
│        → DB: conversation_responses table                         │
│                                                                    │
│     c. Emit to Redis:                                             │
│        await event_publisher.publish_event(                       │
│            transaction_id=transaction_id,                         │
│            event_type="streaming.complete",                       │
│            data={                                                 │
│                "original": original_response,                     │
│                "final": final_response,                           │
│                "chunks_received": len(original_chunks),           │
│                "chunks_sent": len(final_chunks),                  │
│            }                                                      │
│        )                                                          │
│        → Redis: real-time UI update                               │
│                                                                    │
└───────────────────────────────────────────────────────────────────┘
```

---

## Format Conversion Points

**Key Principle:** Policies always work with OpenAI format (our internal common format)

```
Client Request (OpenAI or Anthropic)
    ↓
[STEP 2d: FORMAT CONVERSION INPUT]
    IF Anthropic: anthropic_to_openai_request()
    ELSE: pass through
    ↓
OpenAI Format (Internal Common Format)
    ↓
[STEP 3-10: POLICY PROCESSING]
    Policy sees OpenAI format throughout:
    - Request processing
    - Response processing
    - Streaming chunks (all OpenAI ModelResponse)
    ↓
OpenAI Format (Policy Output)
    ↓
[STEP 11: FORMAT CONVERSION OUTPUT]
    IF client wants Anthropic: openai_chunk_to_anthropic_chunk()
    ELSE: pass through
    ↓
Client Format (OpenAI or Anthropic)
    ↓
[STEP 12: SEND TO CLIENT]
```

**Why this design?**

- LiteLLM outputs OpenAI format (standardized)
- Policies work with one consistent format
- Format conversion at edges (input/output) keeps policies simple
- Client gets their expected format

---

## State Tracking Map

### Where State Lives

| State Object | Location | Contains | Lifetime |
|--------------|----------|----------|----------|
| **StreamState** | StreamingChunkAssembler | `blocks`, `current_block`, `just_completed`, `finish_reason`, `raw_chunks` | Per streaming response |
| **TransactionRecorder** | Gateway layer | `original_request`, `final_request`, `original_chunks`, `final_chunks` | Per transaction |
| **PolicyContext** | ControlPlane | `transaction_id`, `span`, `request`, `scratchpad` | Per request/response pair |
| **StreamingContext** | EventBasedPolicy | `policy_context`, `stream_state`, `outgoing_queue`, `_output_finished` | Per streaming response |

### Data Flow

```
Original Request
    ↓
TransactionRecorder.original_request ✓ Recorded

    ↓ (after policy)

Final Request (sent to LLM)
    ↓
TransactionRecorder.final_request ✓ Recorded

    ↓ (LLM responds)

Raw Chunks (from LLM)
    ↓
TransactionRecorder.original_chunks[] ✓ Buffered
StreamState.raw_chunks[] ✓ Buffered (for policy access)

    ↓ (assembled)

StreamState.blocks[] (ContentStreamBlock, ToolCallStreamBlock)
    ↓ (policy processes)

Policy Hooks (full access to StreamState)
    ↓ (policy outputs)

Modified Chunks (to client)
    ↓
TransactionRecorder.final_chunks[] ✓ Buffered

    ↓ (after streaming)

Finalize: Emit original vs final to DB/Redis ✓ Recorded
```

---

## Achieving Design Goals

### Goal 1: Generate transaction ID and record original request ✅

**Where:** Gateway (step 2)
```python
transaction_id = str(uuid4())
recorder = TransactionRecorder(transaction_id, db_pool, event_publisher)
recorder.record_original_request(request)
```

### Goal 2: Apply policy to request ✅

**Where:** ConversationHandler (step 3)
```python
policy_decision = await policy.process_request(request, context)
```

### Goal 3: Policy decides next step ✅

**Where:** ConversationHandler (step 3c)
```python
if policy_decision.action == "send_to_backend":
    recorder.record_final_request(policy_decision.request)
    # Proceed to backend
elif policy_decision.action == "immediate_response":
    recorder.record_immediate_response(policy_decision.response)
    # Skip to send to client
```

### Goal 4: Record sent request OR immediate response ✅

**Where:** ConversationHandler (step 3c)
- Either `recorder.record_final_request()` OR
- `recorder.record_immediate_response()`

### Goal 5: Record original response ✅

**Where:** TransactionRecorder.wrap_incoming() (step 5)
```python
async for chunk in llm_stream:
    self.original_chunks.append(chunk)  # ✓ Buffered
    yield chunk
```

### Goal 6: Apply policy to response with full stream state ✅

**Where:** EventBasedPolicy hooks (step 9)

Hooks receive:
- `stream_state` with ALL blocks + raw chunks
- Called on: chunk received, block complete, stream complete

Example:
```python
async def on_tool_call_complete(self, stream_state, context, streaming_ctx):
    # Access full state
    all_blocks = stream_state.blocks
    completed_block = stream_state.just_completed
    all_tool_calls = stream_state.get_completed_tool_calls()

    # Make decision
    if len(all_tool_calls) >= 3:
        streaming_ctx.mark_output_finished()
```

### Goal 7: Record final response ✅

**Where:** TransactionRecorder.wrap_outgoing() (step 10)
```python
async for chunk in policy_stream:
    self.final_chunks.append(chunk)  # ✓ Buffered
    yield chunk
```

**Where:** TransactionRecorder.finalize() (step 13)
```python
emit_response_event(
    original_response=reconstruct_from_chunks(original_chunks),
    final_response=reconstruct_from_chunks(final_chunks),
)
```

### Goal 8: Incoming/outgoing streams fully decoupled ✅

**How:** Policy hooks don't return values - they push to outgoing

```python
# Policy can send 0 chunks (block output)
async def on_content_delta(self, delta, stream_state, context, streaming_ctx):
    if should_block:
        return  # Send nothing!

# Policy can send 1 chunk (normal)
async def on_content_delta(self, delta, stream_state, context, streaming_ctx):
    await streaming_ctx.send_text(delta)

# Policy can send N chunks (fan-out)
async def on_content_complete(self, stream_state, context, streaming_ctx):
    content = stream_state.just_completed.content
    for part in content.split('\n\n'):
        await streaming_ctx.send_text(part)
        await streaming_ctx.send_text("\n---\n")
```

---

## Key Architectural Decisions

### 1. Incoming/Outgoing Decoupling

**Decision:** EventBasedPolicy hooks return `None`, push to outgoing via `streaming_ctx.send()`

**Rationale:**
- Policies can send 0, 1, or many outputs per input
- Clear separation: input processing vs output generation
- Supports advanced use cases (filtering, fan-out, blocking)

### 2. StreamState as Single Source of Truth

**Decision:** StreamState holds `blocks` + `raw_chunks`

**Rationale:**
- Policies need access to full state for decisions
- Can reconstruct complete response from StreamState
- No duplicate buffering needed elsewhere

### 3. TransactionRecorder at Gateway Layer

**Decision:** Wrap streams at gateway level, not in ControlPlane

**Rationale:**
- Separation of concerns: policy execution vs observability
- ControlPlane only executes policies
- Gateway coordinates recording + policy execution

### 4. Queue-Based Processing

**Decision:** Use queues between incoming/policy/outgoing

**Rationale:**
- Enables async processing with timeout monitoring
- Decouples incoming from outgoing
- Allows policy to process at different rate than LLM

### 5. EventBasedPolicy vs SimpleEventBasedPolicy

**Decision:** Two levels of abstraction

**Rationale:**
- EventBasedPolicy: Full control, void returns, manual sending
- SimpleEventBasedPolicy: Simplified, value returns, automatic sending
- Advanced users get power, beginners get simplicity

---

## Non-Streaming Flow (for comparison)

```
1. Client request arrives
2. Gateway: Generate transaction_id, record original request
3. Policy.process_request() → decision
4. If backend: Call LiteLLM non-streaming
5. Record original response
6. Policy.process_full_response(response) → modified response
7. Record final response
8. Send to client
9. Finalize recording (emit to DB/Redis)
```

**Differences from streaming:**
- No queues (just direct calls)
- No StreamState (just ModelResponse)
- No assembler (response is already complete)
- Simpler but same recording pattern

---

## Questions for Implementation

### 1. ConversationHandler or inline?

**Option A:** Create `ConversationHandler` class
```python
class ConversationHandler:
    def __init__(self, policy, recorder):
        self.policy = policy
        self.recorder = recorder

    async def process(self, request):
        # Steps 2-13
```

**Option B:** Keep logic in gateway functions
```python
# In gateway_routes.py
async def handle_request(request):
    recorder = ConversationRecorder(...)
    recorder.record_original_request(request)
    # ... rest inline
```

**Recommendation:** Start with Option B (inline), extract later if needed

### 2. PolicyDecision object?

**Question:** Should `process_request()` return structured decision?

**Option A:** Structured return
```python
@dataclass
class PolicyDecision:
    action: Literal["send_to_backend", "immediate_response"]
    request: Request | None
    response: ModelResponse | None
```

**Option B:** Just return modified request (current)
```python
async def process_request(self, request, context) -> Request:
    return modified_request  # Always send to backend
```

**Recommendation:** Start with Option B, add PolicyDecision if needed

### 3. Where to create StreamingContext.state?

**Question:** When to pass StreamState to StreamingContext?

**Current:** StreamingContext created before StreamingChunkAssembler
**Problem:** StreamState doesn't exist yet!

**Solution:** Pass StreamState reference after creating assembler
```python
assembler = StreamingChunkAssembler(...)
streaming_ctx.state = assembler.state  # Add reference
```

### 4. Non-streaming: should it use blocks too?

**Question:** Should non-streaming also convert to blocks?

**Option A:** Use same block abstraction
```python
# Convert ModelResponse → blocks → policy → blocks → ModelResponse
```

**Option B:** Keep separate (current)
```python
# Just pass ModelResponse directly
```

**Recommendation:** Option B for now (simpler)

---

## Next Steps

1. ✅ Update EventBasedPolicy hooks to receive `stream_state`
2. ✅ Add `raw_chunks` to StreamState
3. ✅ Update StreamingChunkAssembler to store chunks in state
4. ✅ Remove `on_complete` from StreamingOrchestrator
5. Create TransactionRecorder wrapper functions
6. Update gateway to use recorder wrappers
7. Simplify ControlPlane (remove buffering/events)
8. Test end-to-end flow
