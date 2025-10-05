# Streaming Rewrite Plan: Bidirectional LiteLLM ↔ Control Plane

**Status**: Implementation Plan
**Created**: 2025-10-05
**Goal**: Rewrite `async_post_call_streaming_iterator_hook` to implement clean bidirectional streaming protocol

## Executive Summary

The current streaming implementation is fundamentally confused about its role: it thinks it's "transforming" upstream responses when architecturally the control plane generates **independent responses**. This mismatch has caused timeout issues, race conditions, and unclear failure modes.

We're rewriting from scratch to implement a clean protocol where:
- **Control plane is the canonical source of responses**
- Client receives NOTHING unless control plane explicitly sends it
- Clear completion semantics (FINAL_END signal)
- Proper timeout and failure handling (return empty response)

## Core Architectural Principle

**The control plane generates its own response, informed by (but independent of) the upstream LLM response.**

This is NOT a transformation pipeline. The two streams are architecturally separate:
- **Original stream**: Upstream LLM → Proxy → Control Plane
- **Final stream**: Control Plane → Proxy → Client

They happen to be correlated (NoOp policy copies chunks, Judge policy uses them for evaluation), but the architecture must not assume any relationship.

## Critical Requirements

### 1. Control Plane is Canonical (Deny-by-Default)

**Requirement**: Client receives ONLY what control plane explicitly sends.

**Implications**:
- Proxy NEVER forwards upstream content directly to client
- On any failure (connection, timeout, error): return empty response
- No fallback to passthrough mode

**Why**: Security and control. Prevents unapproved content from leaking. Aligns with AI control principles.

### 2. Bidirectional Communication on Single Connection

**Requirement**: Proxy must send original chunks AND receive final chunks on same WebSocket.

**Constraint**: LiteLLM is behind NAT/firewall - control plane cannot initiate connections.

**Implications**:
- Single WebSocket connection opened by proxy
- Both directions use this connection concurrently
- Connection lifecycle managed by proxy

### 3. Clear Completion Semantics

**Requirement**: Both sides must know when streams are done, no ambiguity.

**Protocol**:
- Control plane sends `{"type": "FINAL_END"}` when policy completes
- Proxy closes connection immediately upon FINAL_END
- No explicit UPSTREAM_END from proxy (control plane detects via `finish_reason`)

**Why**: LiteLLM callback blocks until generator completes. Must exit cleanly when control plane is done, even if upstream still streaming.

### 4. Timeout for Control Plane Response

**Requirement**: If control plane doesn't respond within timeout, fail gracefully.

**Default**: 30 seconds (configurable)
**Trigger**: No chunks received from control plane within window
**Behavior**: Return empty response to client

**Why**: Control plane might hang, crash, or have slow policy (judge evaluation). Client shouldn't wait forever.

**NOT a timeout**: If control plane actively streaming (sending chunks), no timeout even if >30s total.

## Protocol Design

### Message Types

```python
# Proxy → Control Plane
{"type": "INIT", "data": {stream_id, model, messages, tools, ...}}  # Connection start
{"type": "CHUNK", "data": <original_chunk>}                         # Upstream chunk

# Control Plane → Proxy
{"type": "CHUNK", "data": <final_chunk>}                            # Final response chunk
{"type": "FINAL_END"}                                                # Response complete
{"type": "ERROR", "error": <description>}                           # Policy error
```

### Connection Lifecycle

```mermaid
sequenceDiagram
    participant Client
    participant Proxy
    participant ControlPlane
    participant Upstream

    Note over Proxy,ControlPlane: Phase 1: Connection Initiation
    Client->>Proxy: Streaming request
    Proxy->>ControlPlane: WebSocket INIT
    Proxy->>Upstream: Start request

    Note over Proxy,ControlPlane: Phase 2: Concurrent Streaming
    loop Until FINAL_END or timeout
        Upstream-->>Proxy: Chunk
        Proxy->>ControlPlane: Forward chunk
        ControlPlane-->>Proxy: Final chunk (when ready)
        Proxy->>Client: Yield final chunk
    end

    Note over Proxy,ControlPlane: Phase 3: Completion
    ControlPlane->>Proxy: FINAL_END
    Proxy->>ControlPlane: Close WebSocket
    Proxy->>Client: End stream
    Note over Upstream: May still be streaming (ignored)
```

### Scenario Flows

#### Success: Fast Policy (NoOp)

```mermaid
sequenceDiagram
    participant P as Proxy
    participant C as Control Plane
    participant U as Upstream

    P->>C: INIT
    U-->>P: Chunk #1
    P->>C: CHUNK #1
    C-->>P: CHUNK #1 (immediate)
    P-->>Client: Yield #1

    U-->>P: Chunk #2 (finish_reason=stop)
    P->>C: CHUNK #2
    C-->>P: CHUNK #2 (immediate)
    P-->>Client: Yield #2

    Note over C: Detects finish_reason
    C->>P: FINAL_END
    P->>C: Close WS
    P-->>Client: Complete
```

#### Success: Slow Policy (Judge - Blocks Early)

```mermaid
sequenceDiagram
    participant P as Proxy
    participant C as Control Plane
    participant U as Upstream

    P->>C: INIT
    U-->>P: Chunk #1 (role)
    P->>C: CHUNK #1
    Note over C: Buffer, wait for tool call

    U-->>P: Chunk #2 (tool_call, finish_reason=tool_calls)
    P->>C: CHUNK #2
    Note over C: Detect finish_reason<br/>Start judge evaluation (5s)

    Note over U: May send more chunks
    Note over P: Still forwarding to C
    Note over C: Judge completes: BLOCK

    C-->>P: CHUNK "BLOCKED"
    P-->>Client: Yield "BLOCKED"
    C->>P: FINAL_END
    P->>C: Close WS
    Note over P: Stop reading from upstream
    P-->>Client: Complete
```

#### Failure: Control Plane Timeout

```mermaid
sequenceDiagram
    participant P as Proxy
    participant C as Control Plane
    participant U as Upstream

    P->>C: INIT
    U-->>P: Chunk #1
    P->>C: CHUNK #1
    Note over C: Policy hangs/crashes

    Note over P: Wait 30 seconds...
    Note over P: TIMEOUT

    P->>C: Close WS
    P-->>Client: Empty response<br/>(immediate end)
```

#### Failure: Connection Broken

```mermaid
sequenceDiagram
    participant P as Proxy
    participant C as Control Plane
    participant U as Upstream

    P->>C: INIT
    U-->>P: Chunk #1
    P->>C: CHUNK #1
    Note over C: Control plane crashes
    C--xP: Connection lost

    Note over P: Detect disconnect
    P-->>Client: Empty response<br/>(immediate end)
```

## Implementation Structure

### High-Level Architecture

```
litellm_callback.py
├── ControlPlaneCallback
│   └── async_post_call_streaming_iterator_hook()  ← Main entry point
│       ├── Setup & connection
│       ├── StreamOrchestrator.run()               ← Core logic
│       └── Cleanup & error handling
│
stream_orchestrator.py (NEW)
├── StreamOrchestrator                              ← Manages bidirectional streaming
│   ├── __init__(connection, upstream, timeout)
│   ├── run() → AsyncGenerator                      ← Main loop
│   ├── _forward_upstream()                         ← Task: send chunks to control plane
│   ├── _receive_control_plane()                    ← Task: receive from control plane
│   └── _poll_with_timeout()                        ← Yield chunks with timeout
│
└── StreamState (Enum)                              ← Clean state machine
    ├── ACTIVE
    ├── ENDED (received FINAL_END)
    └── FAILED (error/timeout)
```

### Key Classes

#### StreamOrchestrator

**Responsibility**: Coordinate concurrent upstream forwarding and control plane polling.

```python
class StreamState(Enum):
    ACTIVE = "active"      # Normal operation
    ENDED = "ended"        # Received FINAL_END
    FAILED = "failed"      # Error or timeout

class StreamOrchestrator:
    """Manages bidirectional streaming between upstream LLM and control plane.

    Architecture:
    - Forwards upstream chunks to control plane (fire-and-forget)
    - Polls control plane for final response chunks
    - Yields only control plane chunks to client
    - Handles timeout, FINAL_END, and errors
    """

    def __init__(
        self,
        connection: WebSocketConnection,
        upstream: AsyncIterator[ModelResponseStream],
        timeout: float = 30.0,
    ):
        self.connection = connection
        self.upstream = upstream
        self.timeout = timeout
        self.state = StreamState.ACTIVE
        self.deadline = time.time() + timeout
        self.last_activity = time.time()

    async def run(self) -> AsyncGenerator[ModelResponseStream, None]:
        """Main orchestration loop.

        Yields:
            Final response chunks from control plane

        Raises:
            TimeoutError: If no control plane activity within timeout
        """
        # Launch background task to forward upstream
        forward_task = asyncio.create_task(self._forward_upstream())

        try:
            # Poll control plane and yield chunks
            async for chunk in self._poll_control_plane():
                yield chunk

                if self.state == StreamState.ENDED:
                    break
        finally:
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await forward_task
```

#### Key Methods

```python
async def _forward_upstream(self) -> None:
    """Background task: forward upstream chunks to control plane.

    Continues until:
    - Upstream exhausts
    - State changes to ENDED/FAILED
    - Task is cancelled
    """
    try:
        async for chunk in self.upstream:
            if self.state != StreamState.ACTIVE:
                break

            try:
                await self.connection.send({"type": "CHUNK", "data": chunk})
            except Exception as exc:
                logger.error(f"Failed to forward chunk: {exc}")
                self.state = StreamState.FAILED
                break
    except Exception as exc:
        logger.error(f"Upstream iteration failed: {exc}")
        self.state = StreamState.FAILED

async def _poll_control_plane(self) -> AsyncGenerator[ModelResponseStream, None]:
    """Poll control plane for final response chunks with timeout.

    Yields chunks until:
    - FINAL_END received
    - Timeout exceeded
    - Error received
    - Connection broken
    """
    receive_task = None

    while self.state == StreamState.ACTIVE:
        # Check timeout
        remaining = self.deadline - time.time()
        if remaining <= 0:
            logger.warning("Control plane timeout")
            self.state = StreamState.FAILED
            break

        # Schedule receive if needed
        if receive_task is None or receive_task.done():
            receive_task = asyncio.create_task(self.connection.receive())

        # Wait for message with timeout
        done, _ = await asyncio.wait(
            {receive_task},
            timeout=min(0.1, remaining)  # Poll every 100ms
        )

        if not done:
            continue  # No message yet, loop

        # Process message
        try:
            message = receive_task.result()
        except Exception as exc:
            logger.error(f"Receive failed: {exc}")
            self.state = StreamState.FAILED
            break

        if message is None:
            # Connection closed
            self.state = StreamState.FAILED
            break

        msg_type = message.get("type")

        if msg_type == "CHUNK":
            # Update activity timestamp
            self.last_activity = time.time()
            self.deadline = self.last_activity + self.timeout

            # Yield to client
            chunk_data = message.get("data")
            yield self._normalize_chunk(chunk_data)

        elif msg_type == "FINAL_END":
            self.state = StreamState.ENDED
            break

        elif msg_type == "ERROR":
            logger.error(f"Control plane error: {message.get('error')}")
            self.state = StreamState.FAILED
            break

        # Prepare for next iteration
        receive_task = None
```

### Main Hook Integration

```python
async def async_post_call_streaming_iterator_hook(
    self,
    user_api_key_dict: Any,
    response: AsyncGenerator[ModelResponseStream, None],
    request_data: dict[str, Any],
) -> AsyncGenerator[ModelResponseStream, None]:
    """Forward streaming through control plane.

    Control plane is canonical source - client receives ONLY what control plane sends.
    On any error: return empty response (deny-by-default).
    """
    stream_id = request_data.get("litellm_call_id")
    if not stream_id:
        # No stream ID - return empty
        return

    # Open connection to control plane
    try:
        manager = self._get_connection_manager()
        connection = await manager.get_or_create(
            stream_id,
            self._json_safe(request_data)
        )
    except Exception as exc:
        logger.error(f"Failed to connect to control plane: {exc}")
        return  # Empty response

    # Run orchestrator
    try:
        orchestrator = StreamOrchestrator(
            connection=connection,
            upstream=response,
            timeout=30.0,  # TODO: Make configurable
        )

        async for chunk in orchestrator.run():
            yield chunk

    except Exception as exc:
        logger.error(f"Streaming orchestration failed: {exc}")
        # Return empty (already yielded any chunks before error)

    finally:
        # Always clean up connection
        try:
            await manager.close(stream_id)
        except Exception as exc:
            logger.error(f"Failed to close connection: {exc}")
```

## Critical Implementation Details

### 1. Timeout is Activity-Based, Not Total Duration

**Correct**: Timeout resets when control plane sends a chunk
```python
if msg_type == "CHUNK":
    self.last_activity = time.time()
    self.deadline = self.last_activity + self.timeout  # Reset
    yield chunk
```

**Why**: Policy might stream a long response over >30 seconds. That's fine as long as it's actively sending chunks. We only timeout if NO activity for 30 seconds.

### 2. Detect Original Stream Completion via finish_reason

Control plane must recognize when upstream is done by examining chunk metadata:

```python
# In policy's generate_response_stream()
async for chunk in incoming_stream:
    # Check for completion
    finish_reason = self._extract_finish_reason(chunk)
    if finish_reason:
        # Stream is complete, do final processing
        ...
        return  # Stops generator, triggers FINAL_END
```

Common `finish_reason` values:
- `"stop"` - Normal completion
- `"tool_calls"` - Tool call complete (judge policy cares about this)
- `"length"` - Max tokens reached
- `"content_filter"` - Filtered

**Gotcha**: `finish_reason` location varies by provider and streaming vs. non-streaming.

Streaming format:
```python
{
    "choices": [{
        "index": 0,
        "delta": {"content": "text"},
        "finish_reason": "stop"  # ← Here in streaming
    }]
}
```

Need robust extraction:
```python
def _extract_finish_reason(self, chunk: dict) -> str | None:
    """Extract finish_reason from chunk, handling both streaming and non-streaming."""
    choices = chunk.get("choices", [])
    if not choices:
        return None

    choice = choices[0]

    # Try delta first (streaming)
    finish_reason = choice.get("finish_reason")
    if finish_reason:
        return finish_reason

    # Try message (non-streaming fallback)
    message = choice.get("message", {})
    return message.get("finish_reason")
```

### 3. Empty Response in Streaming Context

When returning empty response due to error/timeout, we simply return from the generator without yielding anything:

```python
async def async_post_call_streaming_iterator_hook(...):
    try:
        # ... orchestration ...
        async for chunk in orchestrator.run():
            yield chunk
    except Exception:
        return  # No more yields = empty response to client
```

LiteLLM will handle this gracefully - client sees immediate end-of-stream.

### 4. Concurrent Task Cancellation

The upstream forwarding task runs in background. MUST be cancelled properly:

```python
forward_task = asyncio.create_task(self._forward_upstream())

try:
    async for chunk in self._poll_control_plane():
        yield chunk
finally:
    # CRITICAL: Cancel background task
    forward_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await forward_task  # Wait for cancellation
```

**Why**: If we don't cancel, task might continue trying to send to closed WebSocket, causing errors.

### 5. WebSocket Send Failures

When forwarding upstream chunk fails, mark as failed but don't crash:

```python
try:
    await self.connection.send({"type": "CHUNK", "data": chunk})
except Exception as exc:
    logger.error(f"Failed to forward chunk: {exc}")
    self.state = StreamState.FAILED
    # Don't raise - let polling loop detect failure state
```

**Why**: Send failure means connection is broken. We want to cleanly exit poll loop and return empty response, not crash with exception.

## Testing Strategy

### 1. Unit Tests for StreamOrchestrator

**Test Cases**:
- Normal flow: upstream chunks → control plane chunks → client
- FINAL_END: stops immediately
- Timeout: no control plane activity for 30s → empty response
- Connection broken: detect and return empty
- Control plane ERROR: return empty
- Upstream faster than control plane: buffering works
- Control plane faster than upstream: works (judge blocking)

### 2. Integration Tests with Mock Control Plane

**Test Cases**:
- Mock control plane that immediately responds (NoOp simulation)
- Mock control plane that delays 5s then responds (Judge simulation)
- Mock control plane that never responds (timeout test)
- Mock control plane that sends ERROR
- Mock control plane that disconnects mid-stream

### 3. E2E Tests with Real LLM Providers

**Critical Test**: Verify `finish_reason` detection works with real providers

```python
@pytest.mark.e2e
async def test_finish_reason_detection_openai():
    """Verify control plane detects stream end from OpenAI finish_reason."""
    # Send real request to OpenAI via proxy
    # Control plane should detect finish_reason="stop"
    # Should send FINAL_END
    # Client should receive complete response

@pytest.mark.e2e
async def test_finish_reason_detection_anthropic():
    """Verify control plane detects stream end from Anthropic finish_reason."""
    # Same as above but with Claude

@pytest.mark.e2e
async def test_finish_reason_detection_tool_calls():
    """Verify control plane detects finish_reason='tool_calls' for judge."""
    # Use GPT-4 with tools
    # Should detect finish_reason="tool_calls"
    # Judge policy should evaluate correctly
```

**Why Critical**: Our protocol assumes `finish_reason` detection works. If providers don't send it consistently, the whole system breaks.

### 4. Existing Test Updates

**E2E parameterized tests** (`test_policies_parameterized.py`):
- Should all pass with new implementation
- Currently 9/10 pass (judge streaming fails due to timeout bug)
- After rewrite: should be 10/10 passing

**Judge-specific tests** (`test_tool_call_judge_e2e.py`):
- Already test streaming and non-streaming
- Should continue to pass

## Migration Path

### Step 1: Create New Module

Create `src/luthien_proxy/proxy/stream_orchestrator.py`:
- `StreamState` enum
- `StreamOrchestrator` class
- Unit tests in `tests/unit_tests/proxy/test_stream_orchestrator.py`

### Step 2: Rewrite Hook

Update `config/litellm_callback.py`:
- Keep existing methods except `async_post_call_streaming_iterator_hook`
- Rewrite hook to use `StreamOrchestrator`
- Remove old `poll_control` nested function
- Remove `passthrough` flag and logic

### Step 3: Update Tests

Fix any broken tests:
- Update assertions if error messages changed
- Update timeout expectations if behavior changed
- Add new tests for empty response behavior

### Step 4: Validate E2E

Run full E2E suite:
```bash
uv run pytest tests/e2e_tests/test_policies_parameterized.py -v -m e2e
```

Should see 10/10 passing.

### Step 5: Real LLM Testing

**CRITICAL**: Test with real LLM backend to verify `finish_reason` detection:
```bash
# Set up real OpenAI key
export OPENAI_API_KEY=sk-...

# Run E2E test against real OpenAI
uv run pytest tests/e2e_tests/test_real_llm_backends.py -v
```

## Potential Gotchas

### 1. finish_reason Not Sent by Provider

**Risk**: Some LLM providers might not send `finish_reason` in every chunk or at all.

**Mitigation**:
- Test with all major providers (OpenAI, Anthropic, local models)
- Document which providers are supported
- Add fallback: if no finish_reason after N chunks, assume complete

### 2. WebSocket Backpressure

**Risk**: If control plane slow to read, WebSocket send buffer might fill up.

**Mitigation**:
- Current implementation already has this issue
- WebSocket library should handle buffering
- If becomes problem: add explicit queue size limit

### 3. Upstream Iterator Reuse

**Risk**: LiteLLM might reuse the `response` iterator in unexpected ways.

**Mitigation**:
- We consume iterator exactly once in `_forward_upstream()`
- Background task ensures we don't block main loop
- Should be fine, but monitor for issues

### 4. Timeout During Long Streaming Response

**Risk**: Policy streams a long response (e.g., 60 seconds of chunks). Our timeout is 30 seconds.

**Solution**: Timeout is **activity-based**, not total duration. As long as chunks arrive, deadline keeps resetting. Only timeout if NO chunks for 30 consecutive seconds.

### 5. Control Plane Sends FINAL_END But Connection Doesn't Close

**Risk**: We set `state = ENDED` but WebSocket doesn't actually close until `finally` block.

**Impact**: Minimal - we stop polling immediately on ENDED. Connection closes shortly after in cleanup.

**Mitigation**: Ensure `finally` block always runs (it will, it's a `finally`).

## Success Criteria

### Implementation Complete When:

1. ✅ `StreamOrchestrator` class exists with full implementation
2. ✅ `async_post_call_streaming_iterator_hook` rewritten to use orchestrator
3. ✅ All unit tests pass (including new orchestrator tests)
4. ✅ All E2E tests pass (10/10 in parameterized suite)
5. ✅ Real LLM backend test confirms `finish_reason` detection
6. ✅ Timeout behavior works (empty response after 30s)
7. ✅ Error behavior works (empty response on connection failure)
8. ✅ No `passthrough` mode exists anywhere

### Validation Checklist:

- [ ] NoOp policy streams correctly
- [ ] AllCaps policy transforms correctly
- [ ] SQL protection policy allows SELECT
- [ ] SQL protection policy blocks DROP
- [ ] Judge policy blocks harmful tool calls (streaming and non-streaming)
- [ ] Timeout returns empty response (test with mock slow control plane)
- [ ] Connection failure returns empty response
- [ ] Control plane ERROR returns empty response
- [ ] Real OpenAI streaming works
- [ ] Real Anthropic streaming works (if credentials available)
- [ ] Tool call completion detected (finish_reason="tool_calls")

## Configuration

### Environment Variables

```python
# In config or environment
CONTROL_PLANE_TIMEOUT = 30.0  # Seconds to wait for control plane activity
CONTROL_PLANE_URL = "http://control-plane:8081"  # Control plane endpoint
```

### Future Enhancements (Out of Scope)

- Make timeout configurable per-request
- Add metrics (chunks forwarded, chunks received, timeouts)
- Add circuit breaker for repeated control plane failures
- Support multiple control planes for redundancy
- Add streaming compression for large responses

## References

- Protocol design discussion: [This conversation]
- Original implementation: `config/litellm_callback.py` (lines 242-420)
- WebSocket connection manager: `src/luthien_proxy/proxy/stream_connection_manager.py`
- E2E test suite: `tests/e2e_tests/test_policies_parameterized.py`
- Judge policy: `src/luthien_proxy/policies/tool_call_judge.py`

---

**Next Steps**:
1. Commit current code state
2. Create `stream_orchestrator.py` with unit tests
3. Rewrite `async_post_call_streaming_iterator_hook`
4. Run tests and iterate
