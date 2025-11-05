# Streaming Pipeline Refactor - Architecture Overview

## Step 1: Protocol and Stub Implementations ✅

Created the foundational protocol and stub implementations for the new streaming pipeline architecture.

### Files Created

1. **`src/luthien_proxy/v2/streaming/protocol.py`**
   - `StreamProcessor[T_in, T_out]` protocol - defines interface for all pipeline stages
   - `PolicyContext` - shared mutable state across request/response lifecycle with keepalive support

2. **`src/luthien_proxy/v2/streaming/common_formatter.py`**
   - `OpenAICommonFormatter` - converts OpenAI chunks → common format
   - `AnthropicCommonFormatter` - converts Anthropic chunks → common format

3. **`src/luthien_proxy/v2/streaming/policy_executor.py`**
   - `PolicyExecutor` - block assembly + policy hooks + timeout monitoring
   - `PolicyTimeoutError` exception

4. **`src/luthien_proxy/v2/streaming/client_formatter.py`**
   - `OpenAIClientFormatter` - converts common format → OpenAI SSE
   - `AnthropicClientFormatter` - converts common format → Anthropic SSE

5. **`src/luthien_proxy/v2/orchestration/policy_orchestrator_v2.py`**
   - `PolicyOrchestratorV2` - simplified orchestrator with explicit pipeline
   - `QueueFullError` exception

### Pipeline Architecture

```
Backend LLM Stream
       ↓
[CommonFormatter]
       ↓
common_in_queue: Queue[CommonChunk]
       ↓
[PolicyExecutor] ← wrapped by TransactionRecorder
  (block assembly + policy hooks)
       ↓
common_out_queue: Queue[CommonChunk]
       ↓
[ClientFormatter] ← wrapped by TransactionRecorder
       ↓
sse_queue: Queue[SSEEvent]
       ↓
Gateway yields to client
```

### Key Design Decisions

1. **Dependency Injection**: Gateway instantiates formatters and policy executor, injects into orchestrator
2. **Context Threading**: `PolicyContext` and `ObservabilityContext` created at gateway, passed through entire lifecycle
3. **Recording Boundaries**: `TransactionRecorder` wraps stages that enter/exit common format space
4. **Keepalive Mechanism**: `PolicyContext.keepalive()` resets timeout for long-running policies
5. **Queue Bounds**: Large queues (10000 default) with circuit breaker on overflow
6. **Explicit Types**: Queues are typed (`Queue[CommonChunk]`, etc.) to clarify data contracts

### StreamProcessor Protocol

All pipeline stages implement:
```python
async def process(
    self,
    input_queue: asyncio.Queue[T_in],
    output_queue: asyncio.Queue[T_out],
    policy_ctx: PolicyContext,
    obs_ctx: ObservabilityContext,
) -> None:
    ...
```

### PolicyContext Features

- `transaction_id`: Unique request identifier
- `scratchpad`: Mutable dict for cross-stage policy state
- `keepalive()`: Reset timeout for long-running work
- `time_since_keepalive()`: Seconds since last activity (for timeout monitoring)

### Next Steps

1. **Write unit tests** for each StreamProcessor implementation
2. **Implement** the processors (extract logic from current PolicyOrchestrator)
3. **Update gateway routes** to instantiate contexts and wire pipeline
4. **Test end-to-end** with existing integration tests

### Status

All stub implementations have complete signatures and docstrings. Type checking will show errors (expected - implementations are `pass` stubs). Ready for review before proceeding to testing + implementation.
