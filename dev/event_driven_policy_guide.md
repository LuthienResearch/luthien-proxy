# EventDrivenPolicy DSL Guide

## Overview

The EventDrivenPolicy DSL provides a safe, hook-based abstraction for writing streaming policies. Instead of manually managing queues, lifecycle, and chunk parsing, policies override hooks that fire at specific points in the stream lifecycle.

## Key Benefits

1. **Safety by design**: Hooks cannot accidentally break the stream (no direct queue access)
2. **Clear lifecycle**: Hooks fire in canonical, predictable order
3. **State isolation**: Per-request state prevents cross-request contamination
4. **Reduced complexity**: Focus on "what happens when X arrives" vs queue management

## Core Concepts

### EventDrivenPolicy

Base class that implements queue consumption and chunk parsing. Subclasses override hooks to implement custom behavior.

### StreamingContext

Per-request context passed to all hooks, providing:
- `await context.send(chunk)` - Send chunks to client
- `context.emit(event, summary, ...)` - Emit policy events
- `context.terminate()` - Request graceful termination
- `context.keepalive()` - Prevent timeout during long operations

### Hook Lifecycle

**Stream-level hooks:**
- `on_stream_started(state, context)` - Before first chunk
- `on_stream_closed(state, context)` - After last chunk (always called)
- `on_stream_error(error, state, context)` - On unexpected exceptions

**Per-chunk hooks (canonical order):**
1. `on_chunk_started(raw_chunk, state, context)` - Chunk received
2. `on_role_delta(role, raw_chunk, state, context)` - If delta.role present
3. `on_content_chunk(content, raw_chunk, state, context)` - If delta.content present
4. `on_tool_call_delta(delta, raw_chunk, state, context)` - For each delta.tool_calls[i]
5. `on_usage_delta(usage, raw_chunk, state, context)` - If usage present
6. `on_finish_reason(reason, raw_chunk, state, context)` - If finish_reason present
7. `on_chunk_complete(raw_chunk, state, context)` - Chunk fully processed

## Simple Example: NoOp Pass-Through

```python
from luthien_proxy.v2.streaming import EventDrivenPolicy, StreamingContext

class NoOpPolicy(EventDrivenPolicy):
    """Pass through all chunks unchanged."""

    def create_state(self):
        return None  # No state needed

    async def on_chunk_complete(self, raw_chunk, state, context):
        # Forward every chunk at the end of processing
        await context.send(raw_chunk)
```

**Why this works:**
- Base class calls hooks in canonical order for each chunk
- Most hooks have empty defaults (no-ops)
- We only override `on_chunk_complete` to forward the raw chunk
- Every chunk gets forwarded exactly once

## Selective Forwarding

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

## Buffering Example

```python
from types import SimpleNamespace

class BufferedContentPolicy(EventDrivenPolicy):
    """Buffer content and transform before sending."""

    def create_state(self):
        return SimpleNamespace(buffer="", chunk_count=0)

    async def on_content_chunk(self, content, raw_chunk, state, context):
        # Buffer content but don't send yet
        state.buffer += content
        state.chunk_count += 1

    async def on_finish_reason(self, reason, raw_chunk, state, context):
        # Transform buffered content and send
        if state.buffer:
            transformed = state.buffer.upper()  # Example transformation
            transformed_chunk = create_text_chunk(transformed)
            await context.send(transformed_chunk)

        # Forward finish chunk
        await context.send(raw_chunk)
```

## Termination Examples

### Via context.terminate()

```python
class BlockOnKeywordPolicy(EventDrivenPolicy):
    def __init__(self, keyword: str):
        self.keyword = keyword

    def create_state(self):
        return None

    async def on_content_chunk(self, content, raw_chunk, state, context):
        if self.keyword in content.lower():
            # Send blocked message
            blocked = create_text_chunk(f"Content blocked: contains '{self.keyword}'")
            await context.send(blocked)
            # Terminate gracefully
            context.terminate()
        else:
            # Forward normal content
            await context.send(raw_chunk)
```

### Via TerminateStream exception

```python
from luthien_proxy.v2.streaming import TerminateStream

class BlockOnKeywordPolicy(EventDrivenPolicy):
    def __init__(self, keyword: str):
        self.keyword = keyword

    def create_state(self):
        return None

    async def on_content_chunk(self, content, raw_chunk, state, context):
        if self.keyword in content.lower():
            # Send blocked message
            blocked = create_text_chunk(f"Content blocked: contains '{self.keyword}'")
            await context.send(blocked)
            # Terminate via exception
            raise TerminateStream(f"Blocked keyword: {self.keyword}")
        else:
            await context.send(raw_chunk)
```

Both methods are equivalent - use whichever is more natural for your use case.

## Tool Call Buffering Example

```python
from luthien_proxy.utils.streaming_aggregation import StreamChunkAggregator

class ToolCallJudgePolicy(EventDrivenPolicy):
    """Buffer tool calls, judge them, block if harmful."""

    def create_state(self):
        return SimpleNamespace(
            aggregators={},  # Per-index aggregators
            buffers={},      # Buffered chunks per index
        )

    async def on_tool_call_delta(self, delta, raw_chunk, state, context):
        # Buffer delta for evaluation - don't emit yet
        idx = delta["index"]
        if idx not in state.aggregators:
            state.aggregators[idx] = StreamChunkAggregator()
            state.buffers[idx] = []

        state.aggregators[idx].capture_chunk(raw_chunk.model_dump())
        state.buffers[idx].append(raw_chunk)
        # No output - chunks buffered for later evaluation

    async def on_finish_reason(self, reason, raw_chunk, state, context):
        if reason == "tool_calls":
            # Evaluate all buffered tool calls
            for idx, agg in state.aggregators.items():
                tool_calls = agg.get_tool_calls()
                for tool_call in tool_calls:
                    if context.keepalive:
                        context.keepalive()  # Judge call may take time

                    if await self.judge_blocks(tool_call):
                        # Blocked! Send replacement and terminate
                        await context.send(create_blocked_response())
                        context.terminate()
                        return

                # Passed - flush buffered chunks
                for chunk in state.buffers[idx]:
                    await context.send(chunk)

        # Forward finish_reason chunk
        await context.send(raw_chunk)
```

## API Guarantees

### What hooks CAN do:
- ✅ `await context.send(chunk)` - Send chunks (as many or few as needed)
- ✅ `context.keepalive()` - Prevent timeout during long operations
- ✅ `context.terminate()` - Request graceful termination
- ✅ `context.emit(event, summary, ...)` - Log policy events
- ✅ `raise TerminateStream("reason")` - Terminate via exception

### What hooks CANNOT do (enforced by API):
- ❌ Access incoming queue - Not exposed at all
- ❌ Call `shutdown()` on outgoing - Queue not exposed, only `send()` method
- ❌ Call `get()` on any queue - Queues completely hidden
- ❌ Break lifecycle - Base class owns loop, cleanup, shutdown
- ❌ Emit after termination - `context.send()` raises once terminated

## State Management

### Rules:
1. **Policy instances are shared** - Never store per-request state on `self`
2. **State is request-scoped** - Create via `create_state()`, passed to all hooks
3. **State is mutable** - Safe to mutate (sequential processing, no races)
4. **Use any type** - dataclass, SimpleNamespace, dict, custom class, etc.

### Example:

```python
from dataclasses import dataclass

@dataclass
class MyPolicyState:
    word_count: int = 0
    buffer: str = ""
    seen_finish: bool = False

class MyPolicy(EventDrivenPolicy):
    def create_state(self):
        return MyPolicyState()

    async def on_content_chunk(self, content, raw_chunk, state, context):
        # Safe to mutate state - it's request-scoped
        words = content.split()
        state.word_count += len(words)
        state.buffer += content
        await context.send(raw_chunk)

    async def on_stream_closed(self, state, context):
        # State available in cleanup
        context.emit("word_count", f"Total words: {state.word_count}")
```

## Error Handling

### Unexpected exceptions:
1. Base class catches exception
2. Calls `on_stream_error(exc, state, context)`
3. Calls `on_stream_closed(state, context)` in finally
4. Shutdown outgoing queue in finally
5. Re-raises original exception

### TerminateStream exception:
- Treated as graceful termination (not an error)
- Skips `on_stream_error`
- Calls `on_stream_closed` and performs shutdown
- Does not re-raise

### Hook errors:
- If `on_stream_error` raises: both exceptions logged, original re-raised
- If `on_stream_closed` raises: exception logged and suppressed (shutdown still happens)

## Integration with LuthienPolicy

To use EventDrivenPolicy in the V2 architecture:

```python
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.streaming import EventDrivenPolicy

class MyPolicy(EventDrivenPolicy, LuthienPolicy):
    """My policy using EventDrivenPolicy for streaming."""

    def create_state(self):
        return None

    async def on_chunk_complete(self, raw_chunk, state, context):
        await context.send(raw_chunk)

    # Implement LuthienPolicy non-streaming methods
    async def process_request(self, request, context):
        return request

    async def process_full_response(self, response, context):
        return response
```

The EventDrivenPolicy base class provides `process_streaming_response()` which integrates with the LuthienPolicy interface.

## Testing

Test policies by:
1. Creating mock incoming/outgoing queues
2. Creating mock PolicyContext (use Mock span)
3. Calling `policy.process_streaming_response(incoming, outgoing, context)`
4. Asserting on output chunks

Example:

```python
import asyncio
from unittest.mock import Mock
from luthien_proxy.v2.policies.context import PolicyContext

async def test_my_policy():
    policy = MyPolicy()
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    mock_span = Mock()
    context = PolicyContext(call_id="test", span=mock_span)

    # Add test chunks
    incoming.put_nowait(create_text_chunk("hello"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Assert on output
    output = []
    while not outgoing.empty():
        output.append(outgoing.get_nowait())

    assert len(output) == 1
```

## When to Use EventDrivenPolicy

**Use EventDrivenPolicy when:**
- You need to buffer/transform streaming chunks
- You need to block/allow based on content/tool calls
- You want safe, structured lifecycle management
- You're implementing policy logic that reacts to specific events

**Use manual LuthienPolicy when:**
- You need bespoke streaming control not covered by hooks
- You're doing complex queue orchestration
- You're implementing infrastructure (not policy logic)

## Common Patterns

### Forward all chunks unchanged:
Override `on_chunk_complete` only.

### Transform text content:
Override `on_content_chunk`, buffer/transform, emit via `context.send()`.

### Block tool calls:
Override `on_tool_call_delta` to buffer, `on_finish_reason` to evaluate and block/forward.

### Conditional termination:
Call `context.terminate()` or `raise TerminateStream()` when condition met.

### Long-running operations:
Call `context.keepalive()` before awaiting judge/DB/API calls.

### Cleanup/finalization:
Override `on_stream_closed` to flush buffers, emit metrics, etc.
