# Developer Onboarding: Understanding Luthien

**TL;DR:** Start with [diagrams.md](diagrams.md) for visual flows, then follow the reading path below to learn the codebase step-by-step.

## Visual Overview

👉 **Start with diagrams:** See [diagrams.md](diagrams.md) for visual flows:
- [Non-Streaming Request Flow](diagrams.md#non-streaming-request-flow)
- [Streaming Request Flow](diagrams.md#streaming-request-flow)
- [Hook Timeline (Sequence Diagram)](diagrams.md#hook-timeline-sequence-diagram)
- [Result Handling Pattern](diagrams.md#result-handling-pattern)

Once you've reviewed the diagrams, follow the code reading path below...

---

## Hook Flows (Detailed)

Understanding how hooks work is essential to understanding Luthien. There are 4 hook types:

### 1. Pre-Call Hook (Fire-and-Forget)

**When**: Before request sent to backend
**File**: `config/unified_callback.py:173-190`

```
Client → LiteLLM → async_pre_call_hook()
                   ↓
                   POST /api/hooks/async_pre_call_hook
                   ↓
Control Plane: log → policy.async_pre_call_hook() → log → publish Redis
               ↓
LiteLLM → Backend (request UNCHANGED - no transformation yet)
```

**Current behavior**: Fire-and-forget logging only. Return value ignored.
**TODO**: Implement request transformation (see `dev/TODO.md`).

### 2. Post-Call Success Hook (Can Transform)

**When**: After non-streaming response received
**File**: `config/unified_callback.py:211-243`

```
Backend → Response → LiteLLM
                     ↓
                     async_post_call_success_hook()
                     ↓
                     POST /api/hooks/async_post_call_success_hook
                     ↓
Control Plane: log → policy.async_post_call_success_hook() → transform → log → publish
               ↓ (returns transformed response)
LiteLLM: _apply_policy_response() mutates response in-place
         ↓
Client ← Modified Response
```

**Key**: Policy can return dict to replace response. If `None`, response unchanged.

### 3. Streaming Iterator Hook (Bidirectional WebSocket)

**When**: Streaming response
**Files**:
- Callback: `config/unified_callback.py:290-362`
- Control plane: `src/luthien_proxy/control_plane/streaming_routes.py:385` (policy_stream_endpoint)

#### Protocol

**Messages TO control plane:**
- `{"type": "START", "data": <request>}` - Initiate stream
- `{"type": "CHUNK", "data": <chunk>}` - Forward upstream chunk
- `{"type": "END"}` - Upstream complete

**Messages FROM control plane:**
- `{"type": "CHUNK", "data": <chunk>}` - Policy-transformed chunk
- `{"type": "KEEPALIVE"}` - Reset timeout (during long processing)
- `{"type": "END"}` - Stream complete
- `{"type": "ERROR", "error": <msg>}` - Abort

#### Flow

```
1. Backend → chunk → LiteLLM callback
2. Callback: Send {"type": "CHUNK", "data": chunk} via WebSocket
3. Control plane: chunk → policy.generate_response_stream()
4. Policy: yield transformed_chunk(s)  [0..N chunks per input]
5. Control plane: Send {"type": "CHUNK", "data": transformed}
6. Callback: Receive → normalize → yield to client
```

**Key insight**: Policy stream is **independent** of upstream - can buffer, split, drop, inject chunks.

**Example**: `ToolCallBufferPolicy` receives 5 partial chunks, buffers all, yields 1 complete chunk.

#### Provider Normalization

Callback converts Anthropic SSE → OpenAI format before control plane sees data:

```
Anthropic: event: content_block_delta\ndata: {"delta":{"text":"Hi"}}
             ↓ AnthropicToOpenAIAdapter
OpenAI:    {"choices":[{"delta":{"content":"Hi"}}]}
```

**Benefit**: Policies never see provider-specific formats.

### 4. Post-Call Failure Hook (Fire-and-Forget)

**When**: Backend error
**File**: `config/unified_callback.py:192-209`

Fire-and-forget logging only. No transformation.

---

## Code Reading Path

After understanding hook flows, follow these steps to learn the codebase:

### 1. Start with Non-Streaming

- **Entry point**: [config/unified_callback.py:211](../config/unified_callback.py#L211) (`async_post_call_success_hook`)
  - Shows how callback POSTs to control plane
- **Main handler**: [control_plane/hooks_routes.py:80](../src/luthien_proxy/control_plane/hooks_routes.py#L80) (`hook_generic`)
  - Read the docstring DATAFLOW section
  - Skim the function - don't trace into helpers yet
- **Result handler**: [control_plane/hook_result_handler.py](../src/luthien_proxy/control_plane/hook_result_handler.py) (`log_and_publish_hook_result`)
  - Shows logging → database → Redis flow
- **Stop here** on first read - you understand the full flow

### 2. Then Explore Streaming

- **Entry point**: [config/unified_callback.py:290](../config/unified_callback.py#L290) (`async_post_call_streaming_iterator_hook`)
- **Orchestrator**: [proxy/stream_orchestrator.py](../src/luthien_proxy/proxy/stream_orchestrator.py) (`StreamOrchestrator.run()`)
  - Focus on the `async for` loop - shows bidirectional flow
- **Control plane**: [control_plane/streaming_routes.py:385](../src/luthien_proxy/control_plane/streaming_routes.py#L385) (`policy_stream_endpoint`)
  - Read docstring DATAFLOW section
  - See `_forward_policy_output()` for chunk forwarding logic

---

## Deep Dives

Once you understand the basic flows:

### Provider Normalization
- **File**: [proxy/stream_normalization.py](../src/luthien_proxy/proxy/stream_normalization.py) (`AnthropicToOpenAIAdapter`)
- **Why**: Converts Anthropic SSE events to OpenAI chunk format
- **Impact**: Policies never see provider-specific formats

### Database Schema
- **File**: [prisma/control_plane/schema.prisma](../prisma/control_plane/schema.prisma)
- **Events**: [conversation/events.py](../src/luthien_proxy/control_plane/conversation/events.py) (`build_conversation_events`)
- **Storage**: [conversation/store.py](../src/luthien_proxy/control_plane/conversation/store.py) (`record_conversation_events`)

### Policy API

**Non-Streaming:**

```python
class MyPolicy(LuthienPolicy):
    async def async_pre_call_hook(self, data: dict, **kwargs) -> None:
        # Log/inspect only (return value ignored)
        pass

    async def async_post_call_success_hook(self, response: dict, **kwargs) -> dict | None:
        # Return dict to transform, None to pass through
        response["choices"][0]["message"]["content"] = "[REDACTED]"
        return response
```

**Streaming:**

```python
class MyStreamingPolicy(LuthienPolicy):
    def create_stream_context(self, stream_id: str, request_data: dict) -> StreamPolicyContext:
        return StreamPolicyContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: StreamPolicyContext,
        incoming_stream: AsyncIterator[dict],
    ) -> AsyncIterator[dict]:
        async for chunk in incoming_stream:
            # Transform, buffer, filter, etc.
            yield chunk  # Or yield transformed version
```

**Key**: No 1:1 chunk mapping required. Can yield 0..N chunks per input.

**Example Policy**: [policies/tool_call_buffer.py](../src/luthien_proxy/policies/tool_call_buffer.py) (`ToolCallBufferPolicy`)
- Shows streaming buffering pattern
- Receives 5 partial chunks, buffers all, yields 1 complete chunk
- Good reference for custom policies

---

## Common Questions

**Q: Where does the policy result go?**
A: `hooks_routes.py` → `log_and_publish_hook_result()` → 3 destinations:
   1. `debug_logs` table (via `DEBUG_LOG_QUEUE`)
   2. `conversation_events` table (via `CONVERSATION_EVENT_QUEUE`)
   3. Redis pub/sub channel `luthien:conversation:{call_id}`

**Q: How are streaming chunks forwarded?**
A: Bidirectional WebSocket between callback and control plane:
   - Callback → control plane: `{"type": "CHUNK", "data": <upstream>}`
   - Control plane → callback: `{"type": "CHUNK", "data": <policy_transformed>}`
   - See [stream_orchestrator.py:run()](../src/luthien_proxy/proxy/stream_orchestrator.py) for orchestration logic

**Q: What gets logged?**
A: Every line with `DEBUG_LOG_QUEUE` or `CONVERSATION_EVENT_QUEUE`
   - Original payloads: `f"hook:{hook_name}"`
   - Results: `f"hook_result:{hook_name}"`
   - Search codebase for `DEBUG_LOG_QUEUE.submit` to find all log points

**Q: How do I test my changes?**
A: Three levels:
   1. Unit tests: `uv run pytest tests/unit_tests`
   2. Integration: `uv run pytest tests/integration_tests`
   3. E2E (slow): `uv run pytest -m e2e`

**Q: How do I trace a live request?**
A: See observability docs for docker log commands. Quick reference:

```bash
# Recent calls
curl http://localhost:8081/api/hooks/recent_call_ids?limit=10

# Call snapshot
curl http://localhost:8081/api/hooks/conversation?call_id=abc-123

# Live stream (SSE)
curl -N http://localhost:8081/api/hooks/conversation/stream?call_id=abc-123

# Trace streaming pipeline
docker compose logs --no-color | grep "abc-123" | grep -E "CALLBACK|WebSocket|ENDPOINT|POLICY"
```

---

## Streaming Result Handling

Streaming uses a different approach than non-streaming, optimized to avoid write amplification:

**Non-streaming**: Uses the `log_and_publish_hook_result()` helper function (in [hook_result_handler.py](../src/luthien_proxy/control_plane/hook_result_handler.py))
- Called once after policy processes the complete request
- Logs to debug_logs, records to conversation_events, publishes to Redis
- Three destinations per request

**Streaming**: Uses the `_StreamEventPublisher` class (in [streaming_routes.py](../src/luthien_proxy/control_plane/streaming_routes.py))
- Created once per streaming session
- **Per-chunk**: Only logs to debug_logs (via `record_result()`)
- **At stream end**: Logs summary to debug_logs + publishes to Redis (via `finish()`)
- **Never writes to conversation_events** (avoids database write amplification)

The approaches differ intentionally: streaming sacrifices structured event storage to avoid creating N database rows for N-chunk responses.

---

## Data Structures

Understanding the shape of data at each step:

**Hook Payloads**:
- Non-streaming hooks receive the full LiteLLM callback payload
- See [LiteLLM callback documentation](https://docs.litellm.ai/docs/observability/custom_callback) for exact schemas
- Key fields: `litellm_call_id`, `litellm_trace_id`, `messages`, `model`, `response`, etc.

**Conversation Events** ([conversation/events.py](../src/luthien_proxy/control_plane/conversation/events.py)):
- `ConversationEvent` - stored in database and published to Redis
- Built from hook payloads by `build_conversation_events()`
- Fields: `call_id`, `trace_id`, `event_type`, `timestamp`, `payload`, etc.
- See [schema.prisma](../prisma/control_plane/schema.prisma) for database schema

**Streaming Chunks**:
- OpenAI format: `{"choices": [{"delta": {...}, "index": 0}], "model": "...", ...}`
- Anthropic chunks normalized to OpenAI format via `AnthropicToOpenAIAdapter`
- See [proxy/stream_normalization.py](../src/luthien_proxy/proxy/stream_normalization.py) for normalization logic

**WebSocket Messages** ([proxy/stream_orchestrator.py](../src/luthien_proxy/proxy/stream_orchestrator.py)):
- `{"type": "CHUNK", "data": <chunk>}` - streaming chunk from upstream or policy
- `{"type": "DONE"}` - end of stream signal
- `{"type": "ERROR", "error": <message>}` - error occurred during streaming

---

## Next Steps

- Review [ARCHITECTURE.md](ARCHITECTURE.md) for architectural decisions and component details
- Explore example policies in `src/luthien_proxy/policies/`
- Try modifying `NoOpPolicy` to add simple transformations
- Check `dev/TODO.md` for open tasks and improvement opportunities
