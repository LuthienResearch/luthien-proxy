# Objective: Refactor Streaming Pipeline to Explicit Queue-Based Architecture

## Goal
Simplify `PolicyOrchestrator.process_streaming_response` by making the streaming pipeline explicit, using dependency injection for policy execution and client formatting, and clearly separating concerns.

## Core Vision - SIMPLIFIED

The streaming pipeline processes data through **two stages** connected by typed queues:

1. **PolicyExecutor**: `AsyncIterator[ModelResponse] → policy_out_queue` - Block assembly + policy hooks (recorded)
2. **ClientFormatter**: `policy_out_queue → sse_queue` - Converts ModelResponse to SSE strings (recorded)

Gateway drains `sse_queue` and yields to client.

**Key Insight**: LiteLLM already provides backend chunks in common format (ModelResponse), so no ingress formatting needed!

## Key Principles

- **Dependency Injection**: Gateway injects policy executor and client formatter into orchestrator
- **Explicit Queues**: Typed queues (`Queue[ModelResponse]`, `Queue[str]`) define data contracts between stages
- **Recording at Boundaries**: `TransactionRecorder` wraps both stages
- **Context Threading**: `ObservabilityContext` and `PolicyContext` created at gateway, passed through entire lifecycle
- **Large Bounded Queues**: Queues sized 10000 with circuit breaker on overflow
- **Keepalive in Executor**: PolicyExecutor owns keepalive state, not PolicyContext

## Components

### Pipeline Stages

1. **PolicyExecutor** - Consumes `AsyncIterator[ModelResponse]` directly from backend
   - Owns `BlockAssembler` for building blocks
   - Invokes policy hooks at key moments (chunk_added, block_complete, etc.)
   - Enforces timeout with `keepalive()` method
   - Outputs to `Queue[ModelResponse]`

2. **ClientFormatter** - Converts to client-specific SSE format
   - OpenAI: ModelResponse → OpenAI SSE string
   - Anthropic: ModelResponse → Anthropic SSE string
   - Outputs to `Queue[str]`

### Context Objects
- **ObservabilityContext**: Created at gateway, spans/metrics for entire request lifecycle
- **PolicyContext**: Created at gateway, mutable state (scratchpad, transaction_id) shared across request + response
  - NO keepalive - that's in PolicyExecutor

### PolicyExecutor Responsibilities
- Accept `AsyncIterator[ModelResponse]` from backend LLM
- Block assembly (owns `BlockAssembler` instance)
- Policy hook invocation at key moments
- Timeout enforcement via internal `keepalive()` method
- State management via `PolicyContext.scratchpad`

## Implementation Progress

### ✅ Completed
1. **Define protocols and context objects**
   - ✅ PolicyContext (simplified - no keepalive)
   - ✅ PolicyExecutor interface (with keepalive method)
   - ✅ ClientFormatter interface
   - ✅ DefaultPolicyExecutor stub with keepalive
   - ✅ OpenAI/Anthropic ClientFormatter stubs

2. **Remove CommonFormatter**
   - ✅ Deleted - LiteLLM already provides common format
   - ✅ Updated PolicyExecutor to accept AsyncIterator[ModelResponse]

3. **Add proper type hints**
   - ✅ AsyncIterator[ModelResponse] for backend streams
   - ✅ Queue[ModelResponse] for policy output
   - ✅ Queue[str] for SSE output

4. **Write initial unit tests**
   - ✅ PolicyContext tests (scratchpad, isolation)
   - ✅ DefaultPolicyExecutor keepalive tests

5. **Implement and test ClientFormatter**
   - ✅ OpenAIClientFormatter: ModelResponse → OpenAI SSE format
   - ✅ AnthropicClientFormatter: ModelResponse → Anthropic SSE with event lifecycle
   - ✅ Unit tests for both formatters (12 passing tests, 100% coverage)

6. **Implement PolicyExecutor**
   - ✅ Extract block assembly logic from current orchestrator
   - ✅ Implement policy hook invocation
   - ✅ Implement timeout monitoring with keepalive
   - ✅ 55 passing tests covering all functionality

7. **Refactor PolicyOrchestrator**
   - ✅ Simplify `process_streaming_response` to 2-stage pipeline
   - ✅ Wire up TransactionRecorder at boundaries (TODO noted for full implementation)

8. **Update gateway routes**
   - ✅ Instantiate contexts (`obs_ctx`, `policy_ctx`)
   - ✅ Instantiate policy executor and client formatter
   - ✅ Pass contexts through request/response lifecycle

9. **Add queue bounds and circuit breaker**
    - ✅ Monitor queue sizes (maxsize=10000)
    - ✅ 30s timeout on all queue.put() operations to prevent deadlock

10. **Integration testing**
    - ✅ Test full pipeline end-to-end
    - ✅ Verify timeout behavior
    - ✅ All existing tests pass (309 passed)

## Success Criteria

- [x] PolicyContext simplified (no keepalive, just scratchpad)
- [x] Keepalive logic moved to PolicyExecutor
- [x] CommonFormatter removed (unnecessary)
- [x] Proper type hints throughout
- [x] ClientFormatter fully implemented and tested
- [x] `process_streaming_response` simplified (~30 lines, clear and explicit)
- [x] PolicyExecutor implemented and tested (55 passing tests)
- [x] `ObservabilityContext` and `PolicyContext` thread through entire request lifecycle
- [x] Recording infrastructure in place (TransactionRecorder TODO noted in code)
- [x] Timeout logic with keepalive implemented in PolicyExecutor
- [x] All existing tests pass (309 passed)
- [x] Pipeline structure is clear from reading code

## Architecture Diagram

```
Backend LLM (via LiteLLM)
         ↓
AsyncIterator[ModelResponse] (already common format)
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
