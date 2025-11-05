# Streaming Pipeline Refactor - Implementation Notes

## Final Architecture - SIMPLIFIED ✅

After discovering that LiteLLM already provides backend chunks in common format (ModelResponse), we **removed CommonFormatter entirely**. The simplified pipeline has just 2 stages:

```
Backend LLM (via LiteLLM)
         ↓
AsyncIterator[ModelResponse] ← already common format!
         ↓
    PolicyExecutor (recorded)
    - Block assembly
    - Policy hooks
    - Timeout + keepalive
         ↓
policy_out_queue: Queue[ModelResponse]
         ↓
   ClientFormatter (recorded)
    - OpenAI or Anthropic SSE
         ↓
sse_queue: Queue[str]
         ↓
    Gateway yields to client
```

## Key Design Decisions

1. **No Ingress Formatting**: LiteLLM handles backend-specific formats, gives us ModelResponse
2. **Dependency Injection**: Gateway injects policy executor and client formatter into orchestrator
3. **Keepalive in Executor**: PolicyExecutor owns keepalive state, not PolicyContext
4. **Context Threading**: `PolicyContext` and `ObservabilityContext` created at gateway, threaded through lifecycle
5. **Recording at Boundaries**: `TransactionRecorder` wraps both pipeline stages
6. **Queue Bounds**: Large queues (10000) with circuit breaker on overflow
7. **Explicit Types**: All queues properly typed (Queue[ModelResponse], Queue[str])

## Files Created/Modified

### Protocol & Context
- **`src/luthien_proxy/v2/streaming/protocol.py`**
  - `PolicyContext` - simplified (transaction_id + scratchpad, NO keepalive)

### Policy Execution
- **`src/luthien_proxy/v2/streaming/policy_executor/interface.py`**
  - `PolicyExecutor` protocol with `keepalive()` method
  - Input: `AsyncIterator[ModelResponse]`
  - Output: `Queue[ModelResponse]`

- **`src/luthien_proxy/v2/streaming/policy_executor/default.py`**
  - `DefaultPolicyExecutor` stub with keepalive tracking

### Client Formatting
- **`src/luthien_proxy/v2/streaming/client_formatter/interface.py`**
  - `ClientFormatter` protocol
  - Input: `Queue[ModelResponse]`
  - Output: `Queue[str]` (SSE strings)

- **`src/luthien_proxy/v2/streaming/client_formatter/openai.py`**
  - `OpenAIClientFormatter` stub

- **`src/luthien_proxy/v2/streaming/client_formatter/anthropic.py`**
  - `AnthropicClientFormatter` stub

### Orchestration
- **`src/luthien_proxy/v2/orchestration/policy_orchestrator_new.py`**
  - `PolicyOrchestrator` - simplified to 2-stage pipeline
  - Accepts: policy_executor, client_formatter, transaction_recorder
  - `QueueFullError` exception

### Tests
- **`tests/unit_tests/v2/streaming/test_protocol.py`**
  - PolicyContext tests (scratchpad, isolation)

- **`tests/unit_tests/v2/streaming/policy_executor/test_default.py`**
  - DefaultPolicyExecutor keepalive tests

## Implementation Status

### ✅ Completed
1. Interface-based architecture with proper separation
2. PolicyContext simplified (no keepalive)
3. Keepalive moved to PolicyExecutor
4. CommonFormatter removed (unnecessary)
5. Proper type hints (ModelResponse, SSE strings)
6. Initial unit tests (PolicyContext, keepalive)
7. Directory structure reorganized

### 🔄 Next Steps
1. Write ClientFormatter tests
2. Implement PolicyExecutor (extract from current orchestrator)
3. Implement ClientFormatter (extract SSE formatting)
4. Wire up simplified PolicyOrchestrator
5. Update gateway routes
6. Integration testing

## Type Flow

```python
# Backend → PolicyExecutor
backend: AsyncIterator[ModelResponse]
           ↓
policy_executor.process(backend, policy_out_queue, ...)

# PolicyExecutor → ClientFormatter
policy_out_queue: asyncio.Queue[ModelResponse]
           ↓
client_formatter.process(policy_out_queue, sse_queue, ...)

# ClientFormatter → Gateway
sse_queue: asyncio.Queue[str]
           ↓
async for sse_string in orchestrator._drain_queue(sse_queue):
    yield sse_string  # to client
```

## Design Evolution

### Original Plan (3 stages)
- CommonFormatter: Backend → Common
- PolicyExecutor: Common → Common
- ClientFormatter: Common → SSE

### Realized (2 stages)
- ~~CommonFormatter~~ ← **REMOVED** (LiteLLM handles this)
- PolicyExecutor: AsyncIterator[ModelResponse] → Queue[ModelResponse]
- ClientFormatter: Queue[ModelResponse] → Queue[str]

This simplification:
- Removes 1 pipeline stage
- Removes 1 queue
- Eliminates ~200 lines of unnecessary code
- Makes data flow clearer
- Reduces points of failure

## Current State

All interfaces and stubs are complete with proper types. Tests verify keepalive mechanism and PolicyContext behavior. Ready to implement the actual logic by extracting from the current PolicyOrchestrator.
