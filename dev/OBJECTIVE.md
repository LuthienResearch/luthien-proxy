# Objective: Refactor Streaming Pipeline to Explicit Queue-Based Architecture

## Goal
Simplify `PolicyOrchestrator.process_streaming_response` by making the streaming pipeline explicit, using dependency injection for formatters and policy execution, and clearly separating concerns.

## Core Vision

The streaming pipeline processes data through three stages connected by typed queues:

1. **CommonFormatter**: `backend_stream ’ common_in_queue` - Converts backend-specific chunks to common format
2. **PolicyExecutor**: `common_in_queue ’ common_out_queue` - Block assembly + policy hooks (recorded)
3. **ClientFormatter**: `common_out_queue ’ sse_queue` - Converts common format to SSE events (recorded)

Gateway drains `sse_queue` and yields to client.

## Key Principles

- **Dependency Injection**: Gateway injects formatters and policy executor into orchestrator
- **Explicit Queues**: Typed queues (`Queue[CommonChunk]`, etc.) define data contracts between stages
- **Recording at Boundaries**: `TransactionRecorder` wraps stages that enter/exit common format space
- **Context Threading**: `ObservabilityContext` and `PolicyContext` created at gateway, passed through entire lifecycle
- **Large Bounded Queues**: Queues sized 1000-10000 with circuit breaker on overflow

## Components

### Stream Processor Protocol
```python
class StreamProcessor(Protocol):
    async def process(
        self,
        input_queue: asyncio.Queue,
        output_queue: asyncio.Queue,
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        ...
```

### Three Implementations
1. **CommonFormatter** - Backend-specific logic (OpenAI vs Anthropic chunk formats)
2. **PolicyExecutor** - Owns `BlockAssembler`, invokes policy hooks, enforces timeout, handles keepalive
3. **ClientFormatter** - Converts common chunks to SSE events for client

### Context Objects
- **ObservabilityContext**: Created at gateway, spans/metrics for entire request lifecycle
- **PolicyContext**: Created at gateway, mutable state shared across request + response processing
  - Includes `keepalive()` method for policies to signal active work

### PolicyExecutor Responsibilities
- Block assembly (owns `BlockAssembler` instance)
- Policy hook invocation at key moments (chunk_added, block_complete, etc.)
- Timeout enforcement (implementation-specific, may use `policy_ctx.keepalive()`)
- State management via `PolicyContext`

## Implementation Steps

1. **Define `StreamProcessor` protocol and context objects**
   - Create `StreamProcessor` protocol
   - Update/create `PolicyContext` with keepalive support
   - Ensure `ObservabilityContext` is ready

2. **Extract CommonFormatter implementations**
   - OpenAI chunk ’ common format
   - Anthropic chunk ’ common format

3. **Extract PolicyExecutor implementation**
   - Move block assembly logic
   - Move policy hook invocation
   - Add timeout monitoring with keepalive support
   - Extract from current `_feed_assembler` logic

4. **Extract ClientFormatter implementations**
   - Common format ’ OpenAI SSE
   - Common format ’ Anthropic SSE
   - Extract from current `_drain_egress` logic

5. **Refactor `PolicyOrchestrator`**
   - Accept injected formatters and policy executor
   - Simplify `process_streaming_response` to queue setup + task launching
   - Wire up `TransactionRecorder` at boundaries

6. **Update gateway routes**
   - Instantiate contexts (`obs_ctx`, `policy_ctx`)
   - Instantiate formatters and policy executor based on request
   - Pass contexts to both `process_request` and `process_streaming_response`

7. **Add queue bounds and circuit breaker logic**
   - Configure queue max sizes (1000-10000)
   - Add monitoring/error handling for full queues

8. **Testing**
   - Unit tests for each `StreamProcessor` implementation
   - Integration tests for full pipeline
   - Test timeout and keepalive behavior

## Success Criteria

- [ ] `process_streaming_response` is simplified to ~20 lines of queue setup + task launching
- [ ] Three `StreamProcessor` implementations extracted and tested independently
- [ ] `ObservabilityContext` and `PolicyContext` thread through entire request lifecycle
- [ ] Recording happens at common format boundaries via `TransactionRecorder`
- [ ] Timeout logic with keepalive support works correctly
- [ ] All existing tests pass
- [ ] Pipeline structure is clear and intuitive from reading the code
