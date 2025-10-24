# Simplified Stream Aggregation Design

**Based on**: Real streaming patterns from GPT-5, GPT-4o-mini, Claude Sonnet 4
**Date**: 2025-10-23
**Context**: See `streaming_patterns.md` for detailed chunk examples
**Sample Data**: `gpt_streaming_chunks.txt`, `anthropic_streaming_chunks.txt`

## Problem Statement

Current `ToolCallStreamGate` is overly complex:
- Mixing aggregation with policy concerns (buffering, judgement)
- Tool-call-specific logic that doesn't need to be
- Owns outgoing queue (should be policy responsibility)
- Duplicated completion detection in multiple places
- Hard to extend for new response types

## Core Insight

All streaming responses follow a **sequential block pattern**:
1. Blocks arrive in order: `[content?] → [tool_call_0?] → [tool_call_1?] → ... → [finish]`
2. Blocks do NOT interleave - each streams completely before the next
3. `finish_reason` signals completion of the entire response
4. Both OpenAI and Anthropic follow identical patterns (via LiteLLM normalization)

**Key Discovery** (2025-10-23): Responses are sequences, not nested structures:
- Content block (if present) always comes first
- Tool calls (if present) stream sequentially after content
- Multiple tool calls stream one-by-one (index 0, then 1, then 2...)
- They do NOT stream in parallel or interleave

See [streaming_response_structures.md](streaming_response_structures.md) for empirical evidence.

## Unified API

```python
@dataclass
class ResponseObject:
    """A single aggregated object from the stream.

    Examples:
    - Content message (type="content", id="msg_0")
    - Tool call (type="tool_call", id="call_xyz")
    """
    type: str  # "content" | "tool_call"
    id: str    # Unique ID (tool_call.id or generated)
    aggregator: StreamAggregator  # Accumulates deltas
    is_complete: bool  # Has finish_reason arrived?


@dataclass
class StreamState:
    """Complete aggregation state for a streaming response."""
    objects: dict[str, ResponseObject]  # Key: object.id
    finish_reason: str | None  # Overall stream finish reason


async def on_chunk(
    chunk: ModelResponse,
    stream_state: StreamState,
    policy_state: Any,
    context: StreamingContext
) -> None:
    """Called for every chunk with full aggregation state.

    Policy receives:
    - Current chunk (raw)
    - All aggregated objects (partial or complete)
    - Completion status for each object
    """
```

## Implementation

### 1. Infrastructure (New StreamProcessor)

```python
class StreamProcessor:
    """Processes stream chunks and maintains aggregation state."""

    def __init__(self, on_chunk_callback):
        self.on_chunk = on_chunk_callback
        self.state = StreamState(objects={}, finish_reason=None)

    async def process(self, incoming: Queue[ModelResponse], policy_state: Any, context: StreamingContext):
        """Process stream, calling callback for each chunk."""
        async for chunk in iter_chunks(incoming):
            # Update aggregations
            self._update_state(chunk)

            # Call policy callback
            await self.on_chunk(chunk, self.state, policy_state, context)

            # Check for completion
            if self.state.finish_reason:
                break

    def _update_state(self, chunk):
        """Update aggregation state from chunk."""
        # Parse chunk deltas
        delta = extract_delta(chunk)

        # Update content aggregation
        if delta.get("content"):
            if "content_msg" not in self.state.objects:
                self.state.objects["content_msg"] = ResponseObject(
                    type="content",
                    id="content_msg",
                    aggregator=StreamAggregator(),
                    is_complete=False
                )
            self.state.objects["content_msg"].aggregator.add_content(delta["content"])

        # Update tool call aggregations
        for tc_delta in delta.get("tool_calls", []):
            tc_id = tc_delta.get("id") or f"tc_{tc_delta.get('index', 0)}"

            if tc_id not in self.state.objects:
                self.state.objects[tc_id] = ResponseObject(
                    type="tool_call",
                    id=tc_id,
                    aggregator=StreamAggregator(),
                    is_complete=False
                )

            self.state.objects[tc_id].aggregator.add_tool_call_delta(tc_delta)

        # Check for finish_reason
        finish_reason = extract_finish_reason(chunk)
        if finish_reason:
            self.state.finish_reason = finish_reason
            # Mark all objects complete
            for obj in self.state.objects.values():
                obj.is_complete = True
```

### 2. Policy Usage

```python
class ToolCallJudgePolicy:
    async def process_streaming_response(self, incoming, outgoing, context, keepalive):
        policy_state = {"buffer": defaultdict(list)}

        async def on_chunk(chunk, stream_state, state, ctx):
            # Check if any tool call just completed
            for obj_id, obj in stream_state.objects.items():
                if obj.type == "tool_call" and obj.is_complete:
                    # Get complete tool call
                    tool_call = obj.aggregator.get_tool_call()

                    # Judge it
                    if should_block(tool_call):
                        await ctx.send(create_error_response())
                        ctx.terminate()
                        return

                    # Forward buffered chunks
                    for buffered_chunk in state["buffer"][obj_id]:
                        await ctx.send(buffered_chunk)
                    state["buffer"][obj_id].clear()
                else:
                    # Buffer chunks for incomplete tool calls
                    if obj.type == "tool_call":
                        state["buffer"][obj_id].append(chunk)

            # Forward non-tool-call chunks immediately
            if not any(obj.type == "tool_call" for obj in stream_state.objects.values()):
                await ctx.send(chunk)

        processor = StreamProcessor(on_chunk_callback=on_chunk)
        await processor.process(incoming, policy_state, context)
```

## Benefits of Simplification

1. **No tool-call-specific logic** - content and tool calls use same aggregation path
2. **No completion heuristics** - finish_reason is authoritative
3. **Clear separation** - infrastructure handles aggregation, policy handles decisions
4. **Easier testing** - can test aggregation independently from policy logic
5. **Extensible** - easy to add new response types (thinking blocks, citations, etc.)

## What Gets Removed

From current `ToolCallStreamGate`:
- ❌ Separate content/tool-call handling
- ❌ `_is_tool_call_complete()` heuristic
- ❌ Index-based tracking (use ID instead)
- ❌ Tool-call-specific completion detection
- ❌ Hardcoded buffering logic (moves to policy)

## Migration Path

1. Create new `StreamProcessor` alongside existing gate
2. Write tests for `StreamProcessor` using saved chunks
3. Migrate policies one at a time
4. Remove old `ToolCallStreamGate` when all policies migrated

## Implementation Details

### Chunk Structure Reference

Every chunk has this structure (both OpenAI and Anthropic via litellm):
```python
{
    "id": "chatcmpl-...",
    "created": 1761262910,
    "model": "...",
    "object": "chat.completion.chunk",
    "choices": [{
        "index": 0,
        "delta": {
            "role": "assistant",      # Only in first chunk
            "content": "text",         # Incremental content
            "tool_calls": [{           # Incremental tool call deltas
                "id": "call_xyz",      # Present in first tool call chunk
                "index": 0,            # NOT unique! Use id instead
                "type": "function",
                "function": {
                    "name": "get_weather",  # Present in first chunk
                    "arguments": "{\"loc"   # Incremental, concatenate
                }
            }]
        },
        "finish_reason": null  # null until final chunk
    }]
}
```

### Key Implementation Notes

1. **Tool call ID tracking**: The `id` field may not be present in every delta chunk. Strategy:
   - First chunk with tool_calls has `id` + `name` + empty `arguments`
   - Subsequent chunks have only `index` + incremental `arguments`
   - Map `index` → `id` in first chunk, use for subsequent chunks
   - This is what `StreamChunkAggregator._resolve_tool_call_identifier` does

2. **Content vs Tool calls can coexist**: Claude sends content chunks BEFORE tool call chunks in same response
   - Don't assume mutually exclusive
   - Need separate aggregators for content and each tool call
   - **Content is complete when**: First `tool_calls` delta arrives (or `finish_reason` is set)
   - Once tool_calls start, no more content chunks will arrive
   - Exception: GPT models start with tool_calls immediately (no content phase)

3. **Empty deltas are valid**: Claude sends `tool_calls: [{arguments: ""}]` chunks
   - Don't skip these, they maintain the streaming flow
   - Concatenation handles empty strings correctly

4. **finish_reason values**:
   - `"stop"` - normal completion (content message)
   - `"tool_calls"` - completed with tool calls
   - `"length"` - hit max_tokens limit
   - Always in separate chunk with empty delta

5. **Multiple tool calls**: All have `index: 0`, distinguished by `id`
   - Can arrive in single chunk (non-streaming) or incrementally
   - Aggregator must handle both patterns

### Files to Modify

**Create new**:
- `src/luthien_proxy/v2/streaming/stream_processor.py` - New unified processor

**Update existing**:
- `src/luthien_proxy/v2/streaming/stream_aggregator.py` - May need small tweaks for content aggregation
- `src/luthien_proxy/v2/streaming/__init__.py` - Export new types

**Eventually remove**:
- `src/luthien_proxy/v2/streaming/tool_call_stream_gate.py` - Replace with StreamProcessor
- `src/luthien_proxy/v2/streaming/stream_observer.py` - Subsumed by StreamProcessor

**Test files**:
- `tests/unit_tests/v2/streaming/test_stream_processor.py` - New tests
- Use saved chunks from `dev/context/*_chunks.txt` for fixtures

### Existing Code to Reuse

`StreamChunkAggregator` in `src/luthien_proxy/utils/streaming_aggregation.py`:
- Already handles incremental tool calls correctly
- Has `capture_chunk(chunk_dict)` method
- Tracks `content_parts`, `tool_calls`, `finish_reason`, `role`
- Method `get_accumulated_content()` and `get_tool_calls()`

Can wrap this in `StreamAggregator` class from `src/luthien_proxy/v2/streaming/stream_aggregator.py`.

### Policy Integration Example

Current policy using `ToolCallStreamGate`:
```python
gate = ToolCallStreamGate(
    on_tool_complete=lambda tc: evaluate_and_decide(tc)
)
await gate.process(incoming, outgoing, keepalive)
```

New policy using `StreamProcessor`:
```python
async def on_chunk(chunk, stream_state, state, context):
    # Check for completed tool calls
    for obj_id, obj in stream_state.objects.items():
        if obj.type == "tool_call" and obj.is_complete and obj_id not in state["judged"]:
            tool_call = obj.aggregator.get_tool_call()
            if should_block(tool_call):
                await context.send(error_chunk)
                context.terminate()
                return
            state["judged"].add(obj_id)

    # Forward chunk
    await context.send(chunk)

processor = StreamProcessor(on_chunk_callback=on_chunk)
await processor.process(incoming, {"judged": set()}, context)
```

### Content Completion Detection

The design philosophy is: **Don't try to detect content completion separately from stream completion.**

However, if needed for intermediate processing:

```python
def _update_state(self, chunk):
    """Update aggregation state from chunk."""
    delta = extract_delta(chunk)

    # Detect phase transition: content -> tool calls
    has_tool_calls = delta.get("tool_calls") and len(delta["tool_calls"]) > 0
    has_content_obj = "content_msg" in self.state.objects

    if has_tool_calls and has_content_obj:
        # First tool call arrived - content is now complete
        # (though not officially complete until finish_reason)
        self.state.objects["content_msg"].is_content_phase_complete = True

    # Update content (may be empty string during tool call phase)
    if delta.get("content") is not None:
        # ... add content

    # Update tool calls
    if has_tool_calls:
        # ... add tool call deltas

    # Set official completion
    if extract_finish_reason(chunk):
        for obj in self.state.objects.values():
            obj.is_complete = True
```

**Recommended approach**: Don't add `is_content_phase_complete` unless you have a specific need. Just use `is_complete` based on `finish_reason`.

### Testing Strategy

1. **Unit tests**: Feed saved chunks through `StreamProcessor`
   - Verify aggregation state after each chunk
   - Verify callbacks fire at right times
   - Test both GPT and Claude patterns

2. **Integration tests**: Use `EventDrivenPolicy` style
   - Create policy that uses `StreamProcessor`
   - Verify end-to-end streaming behavior

3. **E2E tests**: Already created in `tests/e2e_tests/test_streaming_aggregation.py`
   - Verify against real models
   - Document any new patterns discovered
