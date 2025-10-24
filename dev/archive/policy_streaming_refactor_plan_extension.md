2025-02-14 — Event-driven policy DSL extension

## Purpose

Build on the existing streaming refactor plan so the optional helper stack grows into a first-class event-driven DSL. The goal is to shrink the cognitive load of policy authors who currently must juggle queues, chunk batching semantics, manual shutdowns, keepalive timing, and "did I forward this chunk already?" bookkeeping. By isolating that machinery, we let authors focus on "what should happen when I see this event?" instead of "how do I safely walk LiteLLM's streaming protocol?".

## Core Contracts

Before implementing the DSL, we must define the interfaces that hooks and the base class depend on.

### Hook Return Values and Stream Control

**Core principle:** Hooks have **direct write access** via `context.send()`. Incoming and outgoing streams are **fully decoupled**.

**Hook signature:**

```python
async def on_content_chunk(
    self,
    content: str,
    raw_chunk: ModelResponse,
    state: PolicyRunState,
    context: StreamingContext
) -> None:
    """Process content chunk.

    Hooks write directly to context.send() to send chunks.
    They may buffer, transform, or discard input chunks as needed.
    No return value - hooks control output explicitly.
    """
    # Examples:
    # 1. Forward as-is
    await context.send(raw_chunk)

    # 2. Buffer and transform
    state.buffer += content
    if should_flush(state.buffer):
        transformed = transform(state.buffer)
        await context.send(create_chunk(transformed))

    # 3. Discard (don't write anything)
    pass  # Chunk is dropped
```

**Stream termination:**

Hooks can terminate the stream in two ways:

1. **Raise TerminateStream exception:**

   ```python
   async def on_tool_call_delta(self, delta, raw_chunk, state, context):
       if should_block(delta):
           await context.send(create_blocked_response())
           raise TerminateStream("Tool call blocked by policy")
   ```

2. **Call context.terminate() (non-exception path):**

   ```python
   async def on_finish_reason(self, reason, raw_chunk, state, context):
       if reason == "tool_calls" and should_block(state.tool_call):
           await context.send(create_blocked_response())
           context.terminate()  # Graceful termination
   ```

**Termination semantics:**

- `TerminateStream` exception: Treated as graceful termination. The base class stops processing after the current hook, skips remaining per-chunk hooks, skips `on_stream_error`, then runs `on_stream_closed` and pump shutdown.
- `context.terminate()`: Sets an internal flag. The flag is checked immediately after the current hook returns; remaining hooks for that chunk are skipped, queued sends flush, new `send()` calls raise, and the pump proceeds to `on_stream_closed` before shutdown.
- Both paths guarantee `on_stream_closed` is called exactly once and the outgoing queue shuts down once.

**Error propagation:**

- Hooks may raise other exceptions; the base class catches them.
- For unexpected exceptions, the base class calls `on_stream_error(exc, state, context)`, then `on_stream_closed`, then performs shutdown before re-raising.
- If `on_stream_error` raises, both exceptions are logged and the original exception is re-raised.
- `on_stream_closed` is ALWAYS called in a finally block regardless of exceptions.

### StreamingContext

Context object passed to every hook, providing access to queues, metadata, and utilities:

```python
@dataclass
class StreamingContext:
    """Per-request context passed to all hooks.

    Attributes:
        request: Original Request object (messages, model, parameters).
        policy_context: PolicyContext for event emission (logging, metrics, debug info).
        keepalive: Optional callback to invoke during long operations (e.g., judge calls)
                   to prevent upstream timeout. Call periodically if hook blocks >1s.
    """
    request: Request
    policy_context: PolicyContext
    keepalive: Callable[[], None] | None = None
    _outgoing: asyncio.Queue[ModelResponse]  # Private - accessed via send()
    _terminate_flag: bool = False  # Internal flag set by terminate()

    async def send(self, chunk: ModelResponse) -> None:
        """Enqueue a chunk for the pump to deliver.

        This is the ONLY way hooks can write to the output stream.
        The outgoing queue itself is not exposed; the pump validates
        invariants (post-terminate guard, instrumentation) before
        forwarding to the queue. Raises if called after terminate_flag
        is set.
        """
        await self._outgoing.put(chunk)

    def emit(self, event_type: str, summary: str, **kwargs) -> None:
        """Emit a policy event for logging/metrics."""
        self.policy_context.emit(event_type, summary, **kwargs)

    def terminate(self) -> None:
        """Request graceful stream termination.

        Sets internal flag; the base class stops processing after the
        current hook completes, skips downstream hooks, and shuts down.
        """
        self._terminate_flag = True
```

**Design rationale:**

- **No `incoming` exposure**: Base class never passes the incoming queue to hooks, making it impossible to call `incoming.get()` or `incoming.shutdown()`
- **No `outgoing` exposure**: Outgoing queue is private (`_outgoing`), replaced with `send()` method that only allows `put()`
- **Safe by design**: Hooks literally cannot call `shutdown()` because they don't have access to the queue object

**API guarantees for EventDrivenPolicy hooks:**

The API is designed to make it **impossible** (not just discouraged) to break the stream.

**What hooks can do:**

- ✅ `await context.send(chunk)` - Send chunks to client (as many or as few as needed)
- ✅ `context.keepalive()` - Prevent timeout during long operations (judge calls, etc.)
- ✅ `context.terminate()` - Request graceful stream termination
- ✅ `context.emit(event, summary, ...)` - Log policy events/metrics
- ✅ `raise TerminateStream("reason")` - Terminate stream via exception

**What hooks CANNOT do (enforced by API):**

- ❌ **Cannot access incoming queue** - Not exposed in `context` at all
- ❌ **Cannot call `shutdown()` on outgoing** - Queue not exposed, only `send()` method
- ❌ **Cannot call `get()` on any queue** - Queues are completely hidden
- ❌ **Cannot break lifecycle** - Base class owns loop, cleanup, and shutdown
- ❌ **Cannot emit after termination** - `context.send()` raises once the termination flag is set

**Comparison with manual policies:**

Manual policies (implementing `LuthienPolicy` directly) receive raw `incoming` and `outgoing` queues and own the entire lifecycle. They can (and must) call `incoming.get()`, `outgoing.put()`, and `outgoing.shutdown()`.

EventDrivenPolicy trades this control for safety: hooks cannot accidentally break the stream.

**Example - Manual vs EventDriven:**

```python
# Manual policy - full control, full responsibility
class ManualPolicy(LuthienPolicy):
    async def process_streaming_response(self, incoming, outgoing, context, keepalive):
        try:
            while True:
                chunk = await incoming.get()  # You own the loop
                await outgoing.put(chunk)      # You control output
        finally:
            outgoing.shutdown()                # You handle cleanup

# EventDrivenPolicy - safe API, base class handles lifecycle
class EventDrivenPolicyImpl(EventDrivenPolicy):
    async def on_content_chunk(self, content, raw_chunk, state, context):
        await context.send(raw_chunk)  # Safe - can only send, not shutdown
        # No access to queues - cannot accidentally break lifecycle
```

### Simple Example: NoOp Pass-Through Policy

To illustrate the core model, here's the simplest possible policy (forwards all chunks unchanged):

```python
class NoOpPolicy(EventDrivenPolicy):
    """Pass through all chunks unchanged."""

    def create_state(self):
        return None  # No state needed

    async def on_chunk_complete(self, raw_chunk, state, context):
        # Forward every chunk at the end of chunk processing
        await context.send(raw_chunk)
```

**Why this works:**

- Base class calls hooks in canonical order for each chunk
- Most hooks have empty defaults (no-ops), so we don't need to override them
- We only override `on_chunk_complete` to forward the raw chunk
- Every chunk gets forwarded exactly once, at the end of processing

**For selective forwarding:**

```python
class ContentOnlyPolicy(EventDrivenPolicy):
    """Forward only chunks with content, drop everything else."""

    def create_state(self):
        return None

    async def on_content_chunk(self, content, raw_chunk, state, context):
        # Forward chunks with content immediately
        await context.send(raw_chunk)

    # Other hooks (on_tool_call_delta, on_finish_reason, etc.) remain no-ops
    # Those chunks are dropped (not forwarded)
```

### LiteLLM Chunk Surface

The DSL must handle the complete surface of fields LiteLLM can emit in streaming chunks:

**Delta fields:**

- `delta.content` (str): Text content delta → triggers `on_content_chunk`
- `delta.role` (str): Role assignment (usually first chunk only) → triggers `on_role_delta`
- `delta.tool_calls` (list): Tool call deltas → triggers `on_tool_call_delta` for each
  - `tool_calls[i].index` (int): Tool call index (for parallel calls)
  - `tool_calls[i].id` (str): Tool call identifier (may be empty in early deltas)
  - `tool_calls[i].type` (str): Call type (typically "function")
  - `tool_calls[i].function.name` (str): Partial function name
  - `tool_calls[i].function.arguments` (str): Partial arguments JSON

**Metadata fields:**

- `finish_reason` (str): Stream end reason ("stop", "tool_calls", "length", etc.) → triggers `on_finish_reason`
- `usage` (dict): Token usage delta (if present) → triggers `on_usage_delta`

**Special cases:**

- Empty chunks (keepalive/ping frames): Chunks with no delta fields → triggers `on_chunk_started` and `on_chunk_complete` only
- Chunks with multiple deltas: Single chunk may contain both `delta.content` and `delta.tool_calls` → triggers multiple hooks in canonical order

**Hook mapping:**

```python
# Canonical order for a chunk with content + tool_call + finish_reason:
on_chunk_started(raw_chunk, state, context)          # Always called
on_role_delta(role, raw_chunk, state, context)       # If delta.role present
on_content_chunk(content, raw_chunk, state, context) # If delta.content present
on_tool_call_delta(delta, raw_chunk, state, context) # For each delta.tool_calls[i]
on_usage_delta(usage, raw_chunk, state, context)     # If usage present
on_finish_reason(reason, raw_chunk, state, context)  # If finish_reason present
on_chunk_complete(raw_chunk, state, context)         # Always called
```

**Dropped fields (explicitly not exposed as hooks):**

- `id`, `object`, `created`, `model`: Static per-stream metadata, available via `context.request`
- Undocumented/internal LiteLLM fields: Logged as warnings if encountered

### Keepalive and Shutdown Semantics

**Keepalive handling:**

- Base class does NOT automatically invoke `context.keepalive()` (hooks control timing)
- Hooks that perform slow operations (judge calls, DB queries) MUST call `keepalive()` before awaiting
- Keepalive frames (empty chunks) trigger `on_chunk_started` and `on_chunk_complete` hooks
- Policies may suppress keepalive frames by not calling `context.send()` in these hooks when chunk is empty

**Shutdown guarantees:**

- The pump calls `outgoing.shutdown()` exactly once from the base class finally block.
- `on_stream_closed(state, context)` is called before shutdown, in the same finally block.
- If an exception occurs:
  1. Base class catches it
  2. Calls `on_stream_error(exc, state, context)`
  3. Calls `on_stream_closed(state, context)` in finally
  4. Pump triggers `outgoing.shutdown()` in finally
  5. Re-raises original exception
- If `on_stream_error` raises, both exceptions are logged, original is re-raised
- If `on_stream_closed` raises, exception is logged and suppressed (shutdown still happens)

**Client disconnect handling:**

- `incoming` queue shutdown triggers immediate cleanup
- Base class catches `asyncio.QueueShutDown`, calls `on_stream_closed`, calls `outgoing.shutdown()`
- No special hook for disconnect; `on_stream_closed` is called with partial state

## Core Components

### 1. EventDrivenPolicy base class

- Implements the queue consumption loop and chunk parsing, exposing a canonical sequence of lifecycle hooks that subclasses override.
- Hooks are called in a fixed, predictable order for each chunk (see Canonical event ordering below).
- Hooks return `None` and call `context.send()` to emit chunks.
- `context.send()` pushes chunks into a base-class-managed pump that enforces invariants (post-terminate guard, single shutdown, instrumentation) before writing to the outgoing queue.
- Pump tracks whether anything was emitted; if the stream ends with no output the base class raises and records telemetry to fail fast.
- Default implementations are no-ops (empty methods), so subclasses opt-in only where they need custom logic.
- Provides a `create_state()` hook for per-request state initialization (returns `PolicyRunState`).
- Pitfall: ensure hooks that terminate the stream (via `context.terminate()` or `raise TerminateStream`) send appropriate responses first.

### 2. Lifecycle guarantees documentation

- Document the canonical hook invocation order for each chunk (see Canonical event ordering below).
- Document stream-level guarantees:
  - Exactly-once `on_stream_started` (first event)
  - `on_stream_closed` OR `on_stream_error` called in finally block (guaranteed even on exceptions)
  - Sequential event processing (next chunk not fetched until current hooks complete)
  - Per-request state isolation (no shared mutable state between concurrent requests)
- Pitfall: hooks must fire deterministically; failing to emit `on_stream_closed` on exceptions would violate the contract.

### 3. Per-request state container

- Provide a `PolicyRunState` protocol (can be dataclass, SimpleNamespace, or custom class).
- Base class calls `create_state()` once per request and passes the state object to every hook.
- Mutable state (buffers, aggregators, counters) lives in this container rather than scattered across local variables.
- Reasoning: Centralizing state makes policies easier to debug (inspect one object), test (assert on state), and understand (explicit data flow).
- Base class injects a reserved `state.completion_aggregator` reference so policies can observe completion events or inspect buffered objects without reassembling deltas.
- Pattern:

  ```python
  class MyPolicy(EventDrivenPolicy):
      def create_state(self):
          return SimpleNamespace(buffer="", count=0)

      async def on_content_chunk(self, content, raw_chunk, state, context):
          # Track state but don't forward yet
          state.buffer += content
          state.count += 1

      async def on_chunk_complete(self, raw_chunk, state, context):
          # Forward chunk at end of processing
          await context.send(raw_chunk)
  ```

- Pitfalls:
  - Policy instances are shared across concurrent requests, so state MUST NOT live on `self`.
  - Sequential event processing guarantees mean state mutations are safe (no race conditions within a request).

### 4. Completion aggregator and derived events

- Base class creates a per-request aggregator and stores it on the state object returned by `create_state()` as `state.completion_aggregator`. Policies can keep whatever extra references they want, but the base ensures one instance per request.
- The aggregator consumes raw LiteLLM deltas in order and emits higher-level events once a full item arrives:
  - `on_content_completed(completed, raw_chunk, state, context)` – fires for every fully formed response unit (assistant text, tool call, red-team content, future types).
  - `on_tool_call_completed(tool_call, raw_chunk, state, context)` – emitted after the generic completion when the completed unit is a tool call.
  - `on_message_completed(message, raw_chunk, state, context)` – emitted after the generic completion when the unit is an assistant message body.
- Completion hooks run after `on_finish_reason` but before `on_chunk_complete`. Policies that only care about completed objects may ignore raw delta hooks and implement the completion hooks instead.
- Aggregator guarantees serial, in-order completion (LiteLLM emits `<thing 1 deltas><thing 2 deltas>…`). Each completion event fires exactly once and includes the final `raw_chunk` that completed the unit for observability.
- Termination semantics apply: if `context.terminate()` fires before completion, pending aggregates are discarded automatically and completion hooks do not run.

### 5. Canonical event ordering per chunk

- Define a fixed sequence of lifecycle hooks called for each chunk in this exact order:
  1. `on_chunk_started(raw_chunk, state, context)` - chunk received from queue (always called)
  2. `on_role_delta(role, raw_chunk, state, context)` - if delta.role present
  3. `on_content_chunk(content, raw_chunk, state, context)` - if delta.content present
  4. `on_tool_call_delta(delta, raw_chunk, state, context)` - for each delta.tool_calls[i] (can be multiple)
  5. `on_usage_delta(usage, raw_chunk, state, context)` - if usage present
  6. `on_finish_reason(reason, raw_chunk, state, context)` - if finish_reason present
  7. `on_chunk_complete(raw_chunk, state, context)` - chunk fully processed (always called)

- Between steps 6 and 7, the completion aggregator may invoke `on_content_completed`, `on_tool_call_completed`, and `on_message_completed` if a unit finishes.

- Stream-level hooks wrap the chunk processing loop:
  - `on_stream_started(state, context)` - before first chunk (always called)
  - `on_stream_closed(state, context)` - after last chunk in finally block (always called)
  - `on_stream_error(error, state, context)` - on unexpected exceptions in the processing loop (skipped for `TerminateStream`)

- Reasoning: Static, predictable order eliminates mental overhead. Authors override only the hooks they need; defaults are no-ops. State always reflects all earlier events in the sequence.
- Pattern:

  ```python
  class ToolCallJudgePolicy(EventDrivenPolicy):
      def create_state(self):
          return SimpleNamespace(aggregators={}, buffers={})

      async def on_tool_call_delta(self, delta, raw_chunk, state, context):
          # Buffer delta for evaluation - don't emit yet
          idx = delta.index
          if idx not in state.aggregators:
              state.aggregators[idx] = StreamChunkAggregator()
              state.buffers[idx] = []
          state.aggregators[idx].capture_chunk(raw_chunk)
          state.buffers[idx].append(raw_chunk)
          # No output - chunks buffered for later evaluation

      async def on_finish_reason(self, reason, raw_chunk, state, context):
          if reason == "tool_calls":
              # Evaluate all buffered tool calls
              for idx, agg in state.aggregators.items():
                  tool_call = extract_tool_call(agg)

                  if context.keepalive:
                      context.keepalive()  # Judge call may take time

                  if await self.judge_blocks(tool_call):
                      # Blocked! Send replacement and terminate
                      await context.send(create_blocked_response())
                      context.terminate()
                      return

                  # Passed - flush buffered chunks via context.send()
                  for chunk in state.buffers[idx]:
                      await context.send(chunk)

          # Forward finish_reason chunk
          await context.send(raw_chunk)
  ```

- Pitfalls:
  - Hook implementations must be fast; blocking in early hooks delays all subsequent hooks for that chunk.
  - Hooks that buffer chunks must ensure they ultimately call `context.send()` (or emit replacements) to avoid hanging the stream.
  - All hooks receive the same `state` object, making data flow explicit but requiring careful state mutation.
  - Default base class behavior: if a hook doesn't call `context.send()`, the chunk is implicitly dropped (not forwarded).

### 6. Testing strategy

- Unit tests for EventDrivenPolicy base class:
  - Hook invocation order matches canonical sequence
  - State isolation between concurrent requests
  - `on_stream_closed` called even on exceptions
  - Termination behavior (both `context.terminate()` and `TerminateStream` exception)
  - Completion aggregator emits each event once, in order, and skips them on termination
  - Pump raises when stream ends without any successful `context.send()` calls
  - `context.send()` raises after termination flag is set
- Integration tests:
  - Refactor UppercaseNthWordPolicy to use EventDrivenPolicy, assert identical output to manual implementation
  - Run concurrent requests through same policy instance, assert no state leakage
- Pitfall: Tests must cover error cases (stream errors, client disconnect, exceptions in hooks) to validate cleanup guarantees.

## Migration suggestion

- After helpers land, refactor ToolCallJudge and the example capitalization policy to the DSL form to provide canonical references. Each refactor is also an opportunity to measure how many lines of imperative control flow we eliminate; that reduction is a proxy for mental overhead.
- Encourage new policies to subclass `EventDrivenPolicy` unless they have bespoke streaming needs; keep `LuthienPolicy` generic for power users.

## Risks

- Over-abstraction: keep the DSL purely optional; policies must be able to drop down to raw `process_streaming_response` when needed.
- Cognitive overhead: ensure documentation highlights when to pick DSL vs manual loops.

## Why this matters for policy complexity

- **Chunk management fatigue:** Today every streaming policy reimplements a loop that consumes an `asyncio.Queue`, buffers chunks, decides when to forward, and remembers to call `outgoing.shutdown()`. Each additional policy introduces another place to get one of those details wrong.
- **Hidden coupling:** Tool-call logic currently mingles queue housekeeping with business rules. Developers must keep the current buffer state, the LiteLLM finish semantics, and the judge decision in working memory simultaneously.
- **Edge-case anxiety:** Authors worry about what happens when the stream ends mid-tool-call or an exception occurs because they own the loop. If the base class owns lifecycle management, authors reason only about the "what do I do when event X arrives?" branch.
- **Discovery cost:** Without shared hooks, knowing "where should I implement this rule?" requires reading other policies. An event-driven DSL supplies obvious extension points, making code review and onboarding easier.
- **Mutable state juggling:** Policies often need to accumulate partial tool calls, rewrite text, or track counters. Without a shared pattern, state ends up spread across local variables in nested loops. A request-scoped state container centralizes that mutation and surfaces it in logs/tests, keeping each handler small and predictable.
- **Sequential reasoning:** By guaranteeing canonical hook order and sequential processing, we spare authors from thinking about races or interleaved mutations. State always reflects all earlier events in the sequence.
- **Cognitive payoff:** Authors tell the story in event-sized pieces ("when finish_reason arrives, judge the buffered tool call") instead of stitching together queue plumbing. Reviewers can skim hook overrides and know they've seen all custom behavior; the base class owns the rest.

## Design decisions

### Default forwarding behavior

- Base-class hook defaults remain no-ops; policies must explicitly call `context.send()` for every chunk they want to emit.
- The pump tracks whether any output was produced. If a policy forgets to emit before the stream closes, the base class raises and surfaces a telemetry event, forcing a fast failure instead of a silent hang.
- Rationale: keeps the contract explicit and enforces decoupling—policies decide when to emit, but the framework guarantees we never return an empty stream unnoticed.

### Instrumentation and error reporting

- Unexpected exceptions trigger the flow documented above: `on_stream_error` (telemetry/logging opportunity), followed by `on_stream_closed`, pump shutdown, and re-raise.
- `TerminateStream` is treated as a success path and bypasses `on_stream_error`.
- Lifecycle events stay minimal (`dsl.stream_started`, `dsl.hook_exception`, `dsl.stream_closed`). Enable per-hook tracing with `DEBUG_DSL_HOOKS=1` when needed.

### Termination flow

- `context.terminate()` and `TerminateStream` both short-circuit the remaining hooks for the current chunk, flush already queued sends, and reject new ones.
- Stream-level ordering is fixed: optional `on_stream_error` (only for unexpected failures), then `on_stream_closed`, then pump shutdown. Documentation and tests must lock this in.

### Completion events

- The per-request aggregator is part of the base class contract. It emits `on_content_completed`, `on_tool_call_completed`, and `on_message_completed` between `on_finish_reason` and `on_chunk_complete`.
- Policies can ignore raw delta hooks and implement only completion hooks when they prefer higher-level signals.
- Aggregator instances are request-scoped, maintain serial ordering, and drop unfinished units automatically on termination.

## Possible next steps

After the core components are implemented and proven with 3-5 policies, consider:

### Decorator-based handler registration

- Provide `@on(EventType)` decorator for use cases requiring multiple handlers per event.
- Initial implementation uses simple method overrides (`on_content_chunk`, `on_finish_reason`, etc.).
- Reasoning: Method overrides are simpler to debug, easier to type-check, and follow familiar patterns. Only add decorators if multiple handlers per event becomes a proven need.
- Pitfall: If decorators are added later, avoid magical metaclass behavior; keep registration explicit and easy to trace.

### Composable rule helpers

- Implement `policy_recipes` module after 5+ policies exist and common patterns are proven.
- Reasoning: Premature abstraction adds complexity without clear benefit. Let duplication emerge organically before extracting shared helpers.
- If common patterns emerge (e.g., buffering, rate-limiting, logging), extract them into small, well-documented combinators.
- Pitfall: Combinators must produce readable stack traces and have clear, documented behavior.
