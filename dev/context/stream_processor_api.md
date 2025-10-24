# Stream Processor API Design

**Date**: 2025-10-23
**Status**: Design approved
**Based on**: OpenAI streaming docs + empirical testing

## Core Types

### StreamBlock Hierarchy

```python
@dataclass
class StreamBlock:
    """Base class for streaming response blocks."""
    id: str
    is_complete: bool = False


@dataclass
class ContentStreamBlock(StreamBlock):
    """Content/message block in a streaming response."""
    content: str = ""  # Accumulated text


@dataclass
class ToolCallStreamBlock(StreamBlock):
    """Tool call block in a streaming response."""
    name: str = ""  # Function name (set in first chunk)
    arguments: str = ""  # Raw JSON string, NOT parsed
    index: int = 0  # Sequential index from stream (0, 1, 2...)
```

### StreamState

```python
@dataclass
class StreamState:
    """Complete state of a streaming response.

    Passed to policy callback on each chunk.
    """
    blocks: list[StreamBlock]  # All blocks in sequential order
    current_block: Optional[StreamBlock]  # Currently streaming block
    just_completed: Optional[StreamBlock]  # Block that completed this chunk
    finish_reason: Optional[str]  # "stop" | "tool_calls" | "length" | None
```

## Callback Signature

```python
async def on_chunk(
    chunk: ModelResponse,
    state: StreamState,
    context: StreamingContext
) -> None:
    """Called for each streaming chunk.

    Args:
        chunk: The raw chunk from the model
        state: Current aggregation state with all blocks
        context: Streaming context with send/terminate methods
    """
```

## Usage Examples

### Basic Policy Pattern

```python
async def on_chunk(chunk, state, context):
    # React to block completions
    if state.just_completed:
        if isinstance(state.just_completed, ToolCallStreamBlock):
            # A tool call just finished streaming
            tool_call = state.just_completed

            # Judge it
            if should_block(tool_call.name, tool_call.arguments):
                await context.send(create_error_response())
                context.terminate()
                return

        elif isinstance(state.just_completed, ContentStreamBlock):
            # Content block finished (rare to care about this)
            pass

    # Access current streaming block
    if state.current_block:
        # Do something with in-progress block
        pass

    # Forward chunk
    await context.send(chunk)
```

### Buffering Until Completion

```python
class BufferedJudgePolicy:
    def __init__(self):
        self.buffered_chunks = []

    async def on_chunk(self, chunk, state, context):
        # Buffer everything until we can judge
        self.buffered_chunks.append(chunk)

        if state.just_completed:
            if isinstance(state.just_completed, ToolCallStreamBlock):
                # Judge the completed tool call
                if should_block(state.just_completed):
                    await context.send(create_error())
                    context.terminate()
                    return

                # Approved - flush buffer
                for buffered in self.buffered_chunks:
                    await context.send(buffered)
                self.buffered_chunks.clear()

        # Continue buffering...
```

### Per-Tool-Call Buffering

```python
async def on_chunk(chunk, state, context):
    # Track which tool calls we've judged
    if not hasattr(context, '_judged_tool_calls'):
        context._judged_tool_calls = set()
        context._tool_buffers = {}

    if state.current_block:
        block_id = state.current_block.id

        if isinstance(state.current_block, ToolCallStreamBlock):
            # Buffer this tool call's chunks
            if block_id not in context._tool_buffers:
                context._tool_buffers[block_id] = []
            context._tool_buffers[block_id].append(chunk)

            # Check if it just completed
            if state.just_completed and state.just_completed.id == block_id:
                # Judge it
                if should_block(state.just_completed):
                    await context.send(create_error())
                    context.terminate()
                    return

                # Approved - flush this tool call's buffer
                for buffered in context._tool_buffers[block_id]:
                    await context.send(buffered)
                del context._tool_buffers[block_id]
                context._judged_tool_calls.add(block_id)
        else:
            # Content block - forward immediately
            await context.send(chunk)
```

## Implementation Notes

### Block IDs

- **Content block**: Always `"content"` (there's only one)
- **Tool call blocks**: Use the `call_id` from the first chunk
  - First chunk has `tool_call.id = "call_xyz123"`
  - Subsequent chunks only have `tool_call.index`
  - Map `index -> id` in the first chunk, use for subsequent

### Block Completion Detection

A block is complete when:
1. Next block starts (different type or different tool call index), OR
2. `finish_reason` is set

**Sequential guarantee**: Only one block can complete per chunk (they stream sequentially).

### Arguments Format

`ToolCallStreamBlock.arguments` is the raw JSON string:
- `'{"location": "Tokyo"}'` (complete)
- `'{"loc'` (incomplete, mid-stream)

Parse when needed:
```python
if state.just_completed and isinstance(state.just_completed, ToolCallStreamBlock):
    try:
        args_dict = json.loads(state.just_completed.arguments)
        # Use args_dict...
    except json.JSONDecodeError:
        # Handle malformed JSON
```

### Empty Strings in Content (Anthropic)

During tool call phase, Anthropic sends `delta.content = ""` (empty string).
These are accumulated but don't change the final content:
```python
"text" + "" + "" == "text"  # Empty strings are harmless
```

## StreamProcessor Interface

```python
class StreamProcessor:
    """Processes streaming chunks and manages block aggregation."""

    def __init__(self, on_chunk_callback: Callable):
        self.on_chunk = on_chunk_callback
        self.state = StreamState(
            blocks=[],
            current_block=None,
            just_completed=None,
            finish_reason=None
        )

    async def process(
        self,
        incoming: AsyncIterator[ModelResponse],
        context: StreamingContext
    ) -> None:
        """Process stream, calling callback for each chunk."""
        async for chunk in incoming:
            # Update state from chunk
            self._update_state(chunk)

            # Call policy callback
            await self.on_chunk(chunk, self.state, context)

            # Clear just_completed for next chunk
            self.state.just_completed = None

            # Check for completion
            if self.state.finish_reason:
                break

    def _update_state(self, chunk: ModelResponse) -> None:
        """Update aggregation state from chunk.

        - Detects block transitions
        - Marks blocks complete
        - Updates current_block and just_completed
        """
        # Implementation details...
```

## Design Decisions (Resolved)

### 1. No `get_tool_call()` helper method
**Decision**: Keep it simple. Policies access `block.name` and `block.arguments` directly.

### 2. Strip empty content from Anthropic tool call chunks
**Decision**: During tool call phase, Anthropic sends `delta.content = ""` (empty string) in every chunk. The StreamProcessor will **remove these empty content fields from chunks before passing to policy** to reduce confusion.

**Rationale**: These empty strings are an Anthropic-specific artifact. Policies shouldn't have to deal with them.

**Implementation**: In `_update_state()`, if we're in tool call phase and `delta.content == ""`, remove it from the chunk before callback.

**Documentation**: This behavior will be documented in-place with a comment explaining why.

### 3. No chunk tracking fields
**Decision**: Don't add `started_at_chunk` or `completed_at_chunk`. Keep the API minimal. Add later if needed for debugging.

### 4. No JSON validation
**Decision**: Keep `arguments` as a raw string. Policies handle `json.JSONDecodeError` if they parse. Infrastructure doesn't validate.

## Next Steps

1. Implement `StreamProcessor._update_state()` logic
2. Write unit tests using saved chunk data
3. Integrate with existing policy infrastructure
4. Migrate tool call judge policy to use new API
