# Luthien V2 Architecture Principles

**Current Status**: V2 architecture is implemented and active. This document captures core design principles.

---

## Core Principles

### 1. Policies are Stateless

Policies define behavior, not state. They contain no instance variables that change per-request.

**Why**: Enables safe concurrent execution, clear data flow, prevents cross-request contamination.

```python
# GOOD - Stateless policy
class MyPolicy(EventDrivenPolicy):
    def __init__(self, threshold: float):
        self.threshold = threshold  # Config only, never changes

    def create_state(self):  # Per-request state
        return SimpleNamespace(buffer=[], count=0)

# BAD - Stateful policy
class BadPolicy(EventDrivenPolicy):
    def __init__(self):
        self.buffer = []  # ❌ Shared across all requests!
```

### 2. PolicyContext is Per-Request

Created once per request-response cycle, passed through all hooks. Contains:
- Request data
- OpenTelemetry span for observability
- Scratchpad for policy-specific state (via `create_state()` in EventDrivenPolicy)

**Why**: Clear lifecycle, automatic cleanup, thread-safe by design.

### 3. Separate Concerns: Context vs Operations

- **Context**: Request data, observability, state management
- **Operations**: StreamingContext for chunk operations (`send()`, `terminate()`, `keepalive()`)

**Why**: Clear API boundaries, prevents misuse (e.g., can't call `shutdown()` on queues directly).

### 4. Event-Driven Policy DSL

Policies define "what happens when X" via hooks:

```python
class ToolCallJudgePolicy(EventDrivenPolicy):
    async def on_tool_call_delta(self, delta, raw_chunk, state, context):
        # Buffer tool call chunks
        state.buffer.append(raw_chunk)

    async def on_finish_reason(self, reason, raw_chunk, state, context):
        # Judge complete tool calls
        if reason == "tool_calls":
            await self._judge_and_maybe_block(state, context)
```

**Why**:
- 30% less code than manual queue management
- Impossible to misuse queues (no direct access)
- Clear intent, easy to test
- Guaranteed lifecycle (e.g., `on_stream_closed` always runs)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    V2 Gateway                        │
│  (Integrated FastAPI + LiteLLM, single process)     │
└─────────────────────────────────────────────────────┘
                        │
                        ├─► OpenTelemetry (traces/logs)
                        ├─► Redis (activity events)
                        └─► PostgreSQL (optional persistence)

Request Flow:
1. Client → FastAPI endpoint (/v1/chat/completions)
2. Create PolicyContext (call_id, span, request)
3. Policy.process_request() - sync hook
4. LiteLLM.acompletion() - upstream call
5. Policy.process_streaming_response() - async streaming hooks
6. Response → Client

Policy Hooks (EventDrivenPolicy):
- on_stream_started(state, context)
- on_chunk_started(raw_chunk, state, context)
- on_content_chunk(content, raw_chunk, state, context)
- on_tool_call_delta(delta, raw_chunk, state, context)
- on_finish_reason(reason, raw_chunk, state, context)
- on_chunk_complete(raw_chunk, state, context)
- on_stream_closed(state, context)  ← ALWAYS runs
```

---

## Key Concepts

### PolicyContext

Per-request context passed to all hooks:

```python
@dataclass
class PolicyContext:
    call_id: str              # Unique request ID
    span: Span                # OpenTelemetry span
    request: Request          # Original client request

    def emit(self, event_type: str, summary: str, **kwargs):
        """Emit observability event to span and Redis"""
```

### StreamingContext

Streaming operations for event-driven hooks:

```python
class StreamingContext:
    async def send(self, chunk: ModelResponse):
        """Send chunk to client"""

    def terminate(self):
        """Stop stream (flushes buffered chunks)"""

    def keepalive(self):
        """Prevent timeout during long operations"""

    def emit(self, event_type: str, summary: str, **kwargs):
        """Emit observability event"""
```

**Safety**: No direct queue access. Hooks cannot call `shutdown()` or `get()`.

### EventDrivenPolicy

Base class for streaming policies with hook-based DSL:

```python
class EventDrivenPolicy(LuthienPolicy):
    def create_state(self):
        """Create per-request state (called once per request)"""
        return SimpleNamespace()  # Or any object

    # Override only the hooks you need:
    async def on_chunk_started(self, raw_chunk, state, context): ...
    async def on_content_chunk(self, content, raw_chunk, state, context): ...
    async def on_stream_closed(self, state, context): ...
```

**Default behavior**: All hooks are no-ops. Override only what you need.

---

## Integration Points

### OpenTelemetry

All requests automatically traced:
- Span attributes: `luthien.call_id`, `luthien.model`, `luthien.stream`, `luthien.policy.name`
- Policy events as span events: `policy.content_filtered`, `policy.tool_call_judged`
- Logs correlated via `trace_id` and `span_id`

### Redis Activity Events

Real-time pub/sub for live monitoring:
```python
context.emit(
    event_type="policy.tool_call_judged",
    summary="Blocked harmful tool call",
    tool_name="execute_shell_command",
    reason="dangerous_operation"
)
```

Consumed by `/v2/activity/monitor` UI for live stream visualization.

### Configuration

Policies configured via YAML:

```yaml
policy:
  class: "luthien_proxy.v2.policies.event_driven_tool_call_judge:EventDrivenToolCallJudgePolicy"
  config:
    model: "ollama/gemma2:2b"
    api_base: "http://local-llm:11434"
    probability_threshold: 0.6
```

---

## Design Decisions

### Why Event-Driven vs Manual Queue Management?

| Aspect | Manual | Event-Driven |
|--------|--------|--------------|
| Lines of code | 798 (policy + gate) | 556 (30% less) |
| Queue access | Direct (risky) | None (safe) |
| Clarity | Mixed concerns | Pure policy logic |
| Testing | Complex setup | Test individual hooks |
| Reusability | Gate per-pattern | Base class for all |

### Why Stateless Policies?

- **Safe concurrency**: No locks needed
- **Clear lifecycle**: State lives in PolicyContext, auto-cleaned
- **Easy testing**: No shared state between tests
- **Predictable**: Same inputs → same outputs

### Why Separate Context/Operations?

- **Clear API**: Context is data, StreamingContext is operations
- **Safety**: Can't misuse internal queues
- **Testability**: Mock StreamingContext for testing

---

## See Also

- [event_driven_policy_guide.md](event_driven_policy_guide.md) - Full DSL guide with examples
- [observability-v2.md](observability-v2.md) - OpenTelemetry integration
- [v2_architecture_design.md](v2_architecture_design.md) - Implementation details
