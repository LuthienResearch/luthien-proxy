# Event-Based Policy V3 - Clean Design

**Date**: 2025-10-23
**Status**: Planning (Clean design)



1. **Policies are stateless**: They define behavior, not track state
2. **PolicyContext is per-request**: Created once per request-response, passed through all hooks
3. **Operations have their own objects**: ResponseWriter for chunk operations, not mixed with context
4. **Consistent event model**: Same paradigm for requests, responses, and streaming

## Design

### 1. PolicyContext - Per-Request State

Extend the existing `PolicyContext` to carry the original request and a scratchpad:

```python
class PolicyContext:
    """Per-request context for policy execution.

    Created once per request-response cycle and passed to all policy hooks.
    Provides request data, observability, and a scratchpad for per-request state.
    """

    call_id: str
    """Unique identifier for this request-response cycle."""

    span: Span
    """OpenTelemetry span for tracing."""

    request: Request
    """Original request from client (ADDED IN V3)."""

    scratchpad: dict[str, Any]
    """Per-request scratchpad for policy-specific state.

    Policies can store arbitrary data here without needing to subclass PolicyContext.
    Common uses:
    - Counters: scratchpad['tool_calls_judged'] = 0
    - Flags: scratchpad['already_warned'] = True
    - Buffers: scratchpad['buffered_text'] = []
    - Metadata: scratchpad['block_reason'] = "harmful tool call"

    Each request gets a fresh empty scratchpad. Data does not persist across requests.
    """

    _event_publisher: RedisEventPublisher | None
    """Optional publisher for real-time UI events."""

    def __init__(
        self,
        call_id: str,
        span: Span,
        request: Request,
        event_publisher: RedisEventPublisher | None = None,
    ):
        self.call_id = call_id
        self.span = span
        self.request = request
        self.scratchpad = {}  # Fresh empty dict per request
        self._event_publisher = event_publisher

    def emit(self, event_type: str, summary: str, **kwargs) -> None:
        """Emit observability event to OTel span and optionally Redis."""
        ...
```

Key changes from current implementation:

- **Add `request: Request` field** so all hooks (including streaming) can access original request
- **Add `scratchpad: dict[str, Any]`** for policy-specific per-request state
- This is passed from `SynchronousControlPlane.process_request()` where Request is available

### 2. StreamingContext - Streaming Operations

Per-request context for streaming hooks with send operations:

```python
class StreamingContext:
    """Per-request context for streaming policy hooks.

    Provides safe, controlled access to stream operations.
    Hooks can send chunks but cannot directly access queues.

    Policies continue processing the incoming stream even after finishing
    output. This allows observability, metrics, and cleanup to occur
    for the entire stream lifecycle.
    """

    policy_context: PolicyContext
    """PolicyContext with request, scratchpad, emit(), etc."""

    keepalive: Callable[[], None] | None
    """Call periodically during long operations to prevent timeout."""

    _outgoing: asyncio.Queue[ModelResponse]
    """Internal queue (not exposed to hooks)."""

    _output_finished: bool
    """Internal flag indicating output stream is complete."""

    async def send(self, chunk: ModelResponse) -> None:
        """Send a raw chunk to client.

        Raises:
            RuntimeError: If called after output stream is finished
        """
        if self._output_finished:
            raise RuntimeError("Cannot send chunks after output stream is finished")
        await self._outgoing.put(chunk)

    async def send_text(self, text: str, finish: bool = False) -> None:
        """Convenience: send text as a chunk.

        Args:
            text: Text content to send
            finish: If True, mark output stream as finished
        """
        chunk = build_text_chunk(
            text,
            model=self.policy_context.request.model,
            finish_reason="stop" if finish else None,
        )
        await self.send(chunk)
        if finish:
            self._output_finished = True

    def mark_output_finished(self) -> None:
        """Mark output stream as finished (no more sends allowed).

        Use this when you've sent a final chunk and want to prevent
        further output, but continue processing input for observability.
        """
        self._output_finished = True

    def is_output_finished(self) -> bool:
        """Check if output stream is finished.

        Returns:
            True if output stream has been marked finished
        """
        return self._output_finished
```

### 2b. Chunk Building Utilities

Utility functions (not methods on a class):

```python
def build_text_chunk(
    text: str,
    model: str,
    finish_reason: str | None = None,
) -> ModelResponse:
    """Build a text content chunk.

    Args:
        text: Text content
        model: Model name
        finish_reason: Optional finish reason ("stop", "length", etc.)

    Returns:
        ModelResponse chunk with text content
    """
    ...

def build_block_chunk(
    block: StreamBlock,
    model: str,
    finish_reason: str | None = None,
) -> ModelResponse:
    """Build a chunk from a completed StreamBlock.

    Args:
        block: ContentStreamBlock or ToolCallStreamBlock
        model: Model name
        finish_reason: Optional finish reason

    Returns:
        ModelResponse chunk with block data
    """
    ...
```

### 3. EventBasedPolicy - Unified Event Model

**Note**: `EventBasedPolicy` inherits from `LuthienPolicy` and provides default implementations of all three required methods (`process_request`, `process_full_response`, `process_streaming_response`). Policies that extend `EventBasedPolicy` only need to override the specific hooks they care about.

```python
class EventBasedPolicy(LuthienPolicy):
    """Base class for event-based policies.

    Policies are stateless - they define behavior.
    Per-request state lives in PolicyContext.

    Provides hooks for all stages:
    - Request: on_request()
    - Non-streaming response: on_response()
    - Streaming response: on_stream_* hooks

    Default implementations pass data through unchanged.
    Override only the hooks you need.
    """

    # ------------------------------------------------------------------
    # Request events
    # ------------------------------------------------------------------

    async def on_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process request before sending to LLM.

        Default: return unchanged.

        Args:
            request: Request to process
            context: Per-request context for events and state

        Returns:
            Modified request (or raise to reject)
        """
        return request

    # ------------------------------------------------------------------
    # Non-streaming response events
    # ------------------------------------------------------------------

    async def on_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process complete (non-streaming) response.

        Default: return unchanged.

        Args:
            response: Complete response from LLM
            context: Per-request context (includes original request)

        Returns:
            Modified response
        """
        return response

    # ------------------------------------------------------------------
    # Streaming response events
    # ------------------------------------------------------------------

    async def on_stream_start(
        self,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called before first chunk is processed.

        Use for initialization, setup, etc.

        Args:
            context: Per-request context (call_id, span, emit)
            streaming_ctx: Streaming context (send, keepalive)
        """
        pass

    async def on_content_delta(
        self,
        delta: str,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called for each content text delta.

        Default: forward delta to client immediately.

        Args:
            delta: Text delta from this chunk
            block: Content block with aggregated content so far
            context: Per-request context
            streaming_ctx: Streaming context
        """
        await streaming_ctx.send_text(delta)

    async def on_content_complete(
        self,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called when content block completes.

        Default: no-op (deltas already forwarded).

        Use this hook for:
        - Metrics/logging after content block finishes
        - Transformations that need the complete content
        - Validations on full content text

        Args:
            block: Completed content block (block.content has full text)
            context: Per-request context
            streaming_ctx: Streaming context
        """
        pass

    async def on_tool_call_delta(
        self,
        raw_chunk: ModelResponse,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called for each tool call delta chunk.

        Default: forward raw chunk to client.

        Override this to prevent forwarding tool call deltas until
        the tool call is complete and evaluated (e.g., for judging).

        Args:
            raw_chunk: Raw chunk with tool call delta
            block: Tool call block with aggregated data so far
            context: Per-request context
            streaming_ctx: Streaming context
        """
        await streaming_ctx.send(raw_chunk)

    async def on_tool_call_complete(
        self,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called when a tool call block completes.

        Default: no-op (deltas already forwarded).

        Use this hook for:
        - Judging/validating complete tool calls
        - Converting block to chunk with build_block_chunk()
        - Metrics/logging after tool call finishes

        If you overrode on_tool_call_delta() to prevent forwarding,
        you must forward the block here (if it passes validation):
            chunk = build_block_chunk(block, model=context.request.model)
            await streaming_ctx.send(chunk)

        Args:
            block: Completed tool call (block.name and block.arguments available)
            context: Per-request context
            streaming_ctx: Streaming context
        """
        pass

    async def on_finish_reason(
        self,
        finish_reason: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called when finish_reason is received.

        Default: send empty chunk with finish_reason.

        Args:
            finish_reason: "stop", "tool_calls", "length", etc.
            context: Per-request context
            streaming_ctx: Streaming context
        """
        await streaming_ctx.send_text("", finish=True)

    async def on_stream_complete(
        self,
        context: PolicyContext,
    ) -> None:
        """Always called in finally block after stream ends.

        Use for cleanup, final events, etc.

        Args:
            context: Per-request context
        """
        pass

    # ------------------------------------------------------------------
    # LuthienPolicy interface implementation
    # ------------------------------------------------------------------

    async def process_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process request - delegates to on_request()."""
        return await self.on_request(request, context)

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming response - delegates to on_response()."""
        return await self.on_response(response, context)

    async def process_streaming_response(
        self,
        incoming: asyncio.Queue[ModelResponse],
        outgoing: asyncio.Queue[ModelResponse],
        context: PolicyContext,
        keepalive: Callable[[], None] | None = None,
    ) -> None:
        """Process streaming response using StreamProcessor and hooks.

        Creates StreamProcessor with callback that dispatches to hooks.
        Manages StreamingContext and lifecycle.

        This is called by StreamingOrchestrator via SynchronousControlPlane.
        """
        # Create streaming context for hooks
        streaming_ctx = StreamingContext(
            policy_context=context,
            keepalive=keepalive,
            _outgoing=outgoing,
            _output_finished=False,
        )

        try:
            # Call stream start hook
            await self.on_stream_start(context, streaming_ctx)

            # Create processor with callback that dispatches to hooks
            async def on_chunk_callback(
                chunk: ModelResponse,
                state: StreamState,
                ctx: Any,  # This is streaming_ctx passed to processor.process()
            ) -> None:
                """Dispatcher from StreamProcessor to policy hooks."""
                # Content delta?
                if state.current_block and isinstance(state.current_block, ContentStreamBlock):
                    delta = self._extract_content_delta(chunk)
                    if delta:
                        await self.on_content_delta(
                            delta, state.current_block, context, streaming_ctx
                        )

                # Content complete?
                if state.just_completed and isinstance(state.just_completed, ContentStreamBlock):
                    await self.on_content_complete(
                        state.just_completed, context, streaming_ctx
                    )

                # Tool call delta?
                if state.current_block and isinstance(state.current_block, ToolCallStreamBlock):
                    await self.on_tool_call_delta(
                        chunk, state.current_block, context, streaming_ctx
                    )

                # Tool call complete?
                if state.just_completed and isinstance(state.just_completed, ToolCallStreamBlock):
                    await self.on_tool_call_complete(
                        state.just_completed, context, streaming_ctx
                    )

                # Finish reason?
                if state.finish_reason:
                    await self.on_finish_reason(
                        state.finish_reason, context, streaming_ctx
                    )

            # Create processor and run
            processor = StreamProcessor(on_chunk_callback=on_chunk_callback)

            # Convert queue to async iterator
            async def queue_to_iter() -> AsyncIterator[ModelResponse]:
                while True:
                    try:
                        chunk = await incoming.get()
                        yield chunk
                    except asyncio.QueueShutDown:
                        break

            await processor.process(queue_to_iter(), streaming_ctx)

        finally:
            # Always call complete hook and shutdown queue
            await self.on_stream_complete(context)
            outgoing.shutdown()

    def _extract_content_delta(self, chunk: ModelResponse) -> str | None:
        """Extract content delta from chunk, if present."""
        if not chunk.choices:
            return None
        delta = chunk.choices[0].delta
        if isinstance(delta, dict):
            return delta.get("content")
        return None
```

## Examples

### Example 1: NoOp Policy

```python
class NoOpPolicy(EventBasedPolicy):
    """Pass everything through unchanged."""
    # No overrides needed - defaults handle everything!
```

### Example 2: Content-Only Policy

```python
class ContentOnlyPolicy(EventBasedPolicy):
    """Forward text content, drop tool calls."""

    async def on_tool_call_delta(self, raw_chunk, block, context, writer):
        # Drop tool call chunks silently
        pass

    async def on_finish_reason(self, finish_reason, context, writer):
        # Always finish with "stop", even if it was "tool_calls"
        await writer.send_text("", finish=True)
```

### Example 3: Tool Call Judge

```python
class ToolCallJudgePolicy(EventBasedPolicy):
    """Judge tool calls with LLM, block if harmful.

    This policy:
    - Auto-forwards content (uses default on_content_delta)
    - Prevents tool call forwarding (overrides on_tool_call_delta with pass)
    - Judges complete tool calls (on_tool_call_complete)
    - Forwards passed tool calls using build_block_chunk()
    - Continues processing stream after blocking for observability
    """

    def __init__(self, judge_config: JudgeConfig):
        self.config = judge_config

    async def on_stream_start(self, context, streaming_ctx):
        # Initialize scratchpad for metrics
        context.scratchpad['tool_calls_judged'] = 0
        context.scratchpad['tool_calls_blocked'] = 0
        context.scratchpad['block_reason'] = None

    # Content hooks: use defaults (forward immediately)
    # async def on_content_delta(...) - inherited default forwards
    # async def on_content_complete(...) - inherited default no-op

    async def on_tool_call_delta(self, raw_chunk, block, context, streaming_ctx):
        """Override default: don't forward - wait for judgment."""
        pass

    async def on_tool_call_complete(self, block, context, streaming_ctx):
        """Judge when tool call is complete."""

        # Early return if output already finished
        if streaming_ctx.is_output_finished():
            # Still log for observability
            context.emit("judge.skipped", f"Skipped {block.name} (output finished)")
            context.scratchpad['tool_calls_skipped'] = \
                context.scratchpad.get('tool_calls_skipped', 0) + 1
            return

        # Keep-alive during slow judge call
        if streaming_ctx.keepalive:
            streaming_ctx.keepalive()

        # Judge the tool call
        result = await self._judge_tool_call(block)
        context.scratchpad['tool_calls_judged'] += 1

        if result.probability >= self.config.probability_threshold:
            # BLOCKED! Send replacement and finish output
            context.emit(
                "judge.blocked",
                f"Blocked {block.name} (p={result.probability:.2f})",
                severity="warning",
            )
            context.scratchpad['tool_calls_blocked'] += 1
            context.scratchpad['block_reason'] = f"{block.name}: {result.explanation}"

            await streaming_ctx.send_text(
                f"⛔ BLOCKED: {block.name} - {result.explanation}",
                finish=True,  # Marks output as finished
            )
            # Continue processing incoming stream for metrics/observability
        else:
            # PASSED - convert block to chunk and send
            context.emit(
                "judge.passed",
                f"Passed {block.name} (p={result.probability:.2f})",
            )
            chunk = build_block_chunk(
                block,
                model=context.request.model,
                finish_reason=None,  # Not finished yet
            )
            await streaming_ctx.send(chunk)

    # Finish hook: use default (sends finish chunk if output not finished)
    # async def on_finish_reason(...) - inherited default

    async def on_stream_complete(self, context):
        """Always called for cleanup/metrics."""
        judged = context.scratchpad.get('tool_calls_judged', 0)
        blocked = context.scratchpad.get('tool_calls_blocked', 0)
        skipped = context.scratchpad.get('tool_calls_skipped', 0)

        context.emit(
            "judge.summary",
            f"Stream complete: {judged} judged, {blocked} blocked, {skipped} skipped",
            details={
                'tool_calls_judged': judged,
                'tool_calls_blocked': blocked,
                'tool_calls_skipped': skipped,
                'block_reason': context.scratchpad.get('block_reason'),
            }
        )

    async def _judge_tool_call(self, block: ToolCallStreamBlock) -> JudgeResult:
        # ... existing judge logic ...
```

## Benefits

1. **Policies are stateless**: No concurrency issues, easy to reason about
2. **State is explicit**: Lives in `PolicyContext.scratchpad`, passed everywhere
3. **Clean separation**: Operations (StreamingContext) vs state (PolicyContext) vs behavior (policy)
4. **Consistent paradigm**: Same event model for all processing stages
5. **Easy to write**: Override only what you need, defaults handle the rest
6. **Continue after output finished**: Policies process entire input stream for observability
7. **No manual buffering**: Use `build_block_chunk()` instead of buffering raw chunks
8. **Sequential execution**: Hooks run in order, no concurrency within a request

## Network Integration

The V3 design interfaces cleanly with the existing gateway architecture:

### Gateway → Control Plane → Policy Flow

```text
1. FastAPI route (/v1/chat/completions)
   ↓
2. process_request_with_policy()
   → control_plane.process_request(request, call_id)
   → policy.process_request(request, context)
   ↓
3. litellm.acompletion(stream=True)
   ↓
4. stream_with_policy_control()
   → control_plane.process_streaming_response(llm_stream, call_id)
   ↓
5. StreamingOrchestrator.process()
   - Creates incoming/outgoing queues
   - Launches policy_processor task:
     → policy.process_streaming_response(incoming, outgoing, context, keepalive)
   ↓
6. EventBasedPolicy.process_streaming_response()
   - Creates StreamingContext with request, keepalive, outgoing queue
   - Creates StreamProcessor with on_chunk_callback dispatcher
   - Calls on_stream_start()
   - Processes chunks → dispatches to hooks
   - Calls on_stream_complete() in finally
   ↓
7. Hooks write to outgoing queue via streaming_ctx.send()
   ↓
8. StreamingOrchestrator yields chunks to gateway
   ↓
9. Gateway serializes to SSE and yields to client
```

### Key Integration Points

1. **PolicyContext gets request**:
   - `SynchronousControlPlane.process_request()` receives `Request` object
   - Creates `PolicyContext(call_id, span, request, event_publisher)`
   - Same context flows to `process_streaming_response()`

2. **StreamingContext bridges policy and orchestrator**:
   - Created in `EventBasedPolicy.process_streaming_response()`
   - Has access to `context.request` (from PolicyContext)
   - Has `_outgoing` queue from orchestrator
   - Has `keepalive` callback from orchestrator
   - Hooks write via `streaming_ctx.send()` → queue

3. **StreamProcessor drives hook dispatch**:
   - Consumes from incoming queue (via async iterator)
   - Tracks block-level state (content, tool calls, completion)
   - Calls policy's `on_chunk_callback` with (chunk, state, streaming_ctx)
   - Policy dispatcher routes to appropriate hooks

## Implementation Plan

1. **Update `PolicyContext` to include `request` and `scratchpad` fields**
   - Add `request: Request` field to `__init__()`
   - Add `scratchpad: dict[str, Any] = {}` field with clear docstring about usage
   - Update `SynchronousControlPlane.process_request()` to pass request
   - Update `SynchronousControlPlane.process_streaming_response()` to pass request

2. **Create `StreamingContext` class** (replaces ResponseWriter concept)
   - Move from event_driven.py or create new module
   - Add `policy_context: PolicyContext` field (remove separate `request` field)
   - Add `send()`, `send_text()` methods
   - Add `is_output_finished()`, `mark_output_finished()` methods
   - Store `policy_context`, `keepalive`, `_outgoing`, `_output_finished`
   - Update `send_text()` to get model from `policy_context.request.model`

3. **Create chunk builder utilities**
   - `build_text_chunk(text, model, finish_reason)`
   - `build_block_chunk(block, model, finish_reason)`
   - Place in `luthien_proxy.streaming.utils` module

4. **Update `EventBasedPolicy` with completion hooks and better docstrings**
   - **Ensure `EventBasedPolicy` inherits from `LuthienPolicy`** (implements required interface)
   - Add `on_content_complete(block, context, streaming_ctx)` hook (default: no-op)
   - Ensure `on_tool_call_complete(block, context, streaming_ctx)` hook exists (default: no-op)
   - Update all hooks to take `streaming_ctx` instead of `writer`
   - Implement default forwarding in `on_content_delta()` and `on_tool_call_delta()`
   - Add docstrings explaining when to use completion hooks and how to forward blocks

5. **Update dispatcher in `process_streaming_response()`**
   - Create `StreamingContext` at start
   - Pass `streaming_ctx` to all hooks
   - Update `on_chunk_callback` to call completion hooks when `state.just_completed`

6. **Write example policies to validate**
   - Update `NoOpPolicy` (no overrides needed!)
   - Update `ToolCallJudgePolicy` to:
     - Use default content forwarding (no override)
     - Override `on_tool_call_delta()` with `pass` to prevent forwarding
     - Judge in `on_tool_call_complete()` and use `build_block_chunk()`
     - Use `streaming_ctx.is_output_finished()` guards
     - Track metrics in `context.scratchpad`
     - Continue processing after blocking for observability
   - Create `ContentTransformPolicy` example

7. **Add comprehensive tests**
   - Test completion hook invocation (content and tool call)
   - Test default forwarding behavior (deltas forward automatically)
   - Test `is_output_finished()` prevents further sends
   - Test policy continues processing after output finished
   - Test scratchpad usage across hooks
   - Test `streaming_ctx.send_text()` convenience
   - Test sequential hook execution (no concurrency)

8. **Deprecate `ToolCallStreamGate`**
   - Mark as deprecated in docstring
   - Add migration guide to docs
   - Plan removal after policies migrated

9. **Update documentation**
   - Update `event_driven_policy_guide.md` with V3 design
   - Add examples of all three tiers (LuthienPolicy, EventBasedPolicy, deprecated gate)
   - Document when to use each tier
