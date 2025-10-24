# Streaming Response Structures

**Date**: 2025-10-23
**Testing**: Live responses from Claude Sonnet 4 and GPT-4o-mini via LiteLLM
**Data**: See `/tmp/*_chunks.json` from `scripts/test_response_structures.py`

## Executive Summary

**Key Finding**: Responses have a **sequential structure**, not a nested one:

```
Response = [content_block] → [tool_call_0] → [tool_call_1] → ... → [tool_call_N] → finish
```

- Content (if present) comes first
- Tool calls (if present) come after, each streaming incrementally
- **They do NOT interleave** - once tool calls start, no more content
- **Multiple tool calls stream sequentially**, not in parallel

## Observed Patterns

### Pattern 1: Content Only (No Tools)

```
Chunk 1:   role=assistant | content="Here"
Chunk 2:   content=" are the major"
...
Chunk 68:  content=" islands."
Chunk 69:  finish=stop
```

**Structure**:
- All chunks contain `delta.content`
- Final chunk has `finish_reason: "stop"`
- No tool calls present

### Pattern 2: Multiple Tool Calls Only (GPT)

```
Chunk 1:   role=assistant | tool_calls=[(idx=0, id=call_W5af..., name=get_weather)]
Chunk 2:   tool_calls=[(idx=0, args='{"lo')]
...
Chunk 6:   tool_calls=[(idx=0, args='"}')]
Chunk 7:   tool_calls=[(idx=1, id=call_aC6p..., name=get_weather)]
Chunk 8:   tool_calls=[(idx=1, args='{"lo')]
...
Chunk 12:  tool_calls=[(idx=1, args='"}')]
Chunk 13:  tool_calls=[(idx=2, id=call_3BDe..., name=get_time)]
...
Chunk 25:  finish=tool_calls
Chunk 26:  (empty delta)
```

**Structure**:
- NO content phase
- Each tool call streams sequentially:
  1. First chunk for tool call N: has `index=N`, `id`, and `name`
  2. Subsequent chunks: have `index=N` and incremental `arguments`
  3. Move to next tool call when arguments complete
- `index` increments for each distinct tool call (0, 1, 2, 3...)
- Final chunk has `finish_reason: "tool_calls"`

### Pattern 3: Content then Multiple Tool Calls (Anthropic)

```
Chunk 1:   role=assistant | content="I'll get the weather"
Chunk 2:   content=" for both Tokyo and London"
Chunk 3:   content="" | tool_calls=[(idx=0, id=toolu_01Pp..., name=get_weather)]
Chunk 4:   content="" | tool_calls=[(idx=0)]
Chunk 5:   content="" | tool_calls=[(idx=0, args='{"location')]
...
Chunk 7:   content="" | tool_calls=[(idx=0, args='"}')]
Chunk 8:   content="" | tool_calls=[(idx=1, id=toolu_01SX..., name=get_time)]
...
Chunk 12:  content="" | tool_calls=[(idx=1, args='"}')]
Chunk 13:  content="" | tool_calls=[(idx=2, id=toolu_01QW..., name=get_weather)]
...
Chunk 22:  finish=tool_calls
```

**Structure**:
- Content phase: chunks 1-2 with actual text
- **Transition**: Chunk 3 has `content=""` (empty string) AND first tool call
- Tool call phase: chunks 3-21, all have `content=""` (empty string)
- Each tool call streams sequentially (same pattern as GPT)
- `index` increments for each distinct tool call

### Pattern 4: Extended Content then Tool Calls (Anthropic)

```
Chunk 1-98:   content chunks (long thinking/explanation)
Chunk 99:     content="" | tool_calls=[(idx=0, id=..., name=get_weather)]
Chunk 100-105: content="" | tool_calls=[(idx=0, args...)]
Chunk 106:    content="" | tool_calls=[(idx=1, id=..., name=get_weather)]
Chunk 107-111: content="" | tool_calls=[(idx=1, args...)]
Chunk 112:    finish=tool_calls
```

**Structure**:
- Same as Pattern 3, just longer content phase
- Confirms content/tool-call phases don't interleave

## Critical Observations

### 1. Sequential, Not Nested

Responses are NOT structured as:
```
ResponseObject = {content: ..., tool_calls: [...]}
```

They ARE structured as a sequence:
```
[ContentBlock] → [ToolCall1] → [ToolCall2] → ... → [Finish]
```

### 2. Tool Calls Stream Sequentially

Multiple tool calls do NOT stream in parallel. They stream one after another:

```
TC0: chunk3(id+name) → chunk4(args) → chunk5(args) → chunk6(args) → chunk7(done)
TC1: chunk8(id+name) → chunk9(args) → chunk10(args) → chunk11(args) → chunk12(done)
TC2: chunk13(id+name) → ...
```

### 3. Index vs ID

- **`index`**: Increments for each distinct tool call (0, 1, 2, 3...)
  - Present in every tool call chunk
  - Sequential across the response
  - **IS unique per tool call** (contrary to design doc assumption!)

- **`id`**: Unique identifier for the tool call
  - Only present in the first chunk for that tool call
  - Subsequent chunks use `index` to identify which tool call they belong to
  - Must maintain `index → id` mapping

### 4. Content Empty Strings (Anthropic Only)

Once tool calls start, Anthropic sends:
- `delta.content = ""` (empty string, NOT null/absent)
- Every subsequent chunk has this empty content field
- This is harmless for aggregation: `"text" + "" + "" = "text"`

GPT does NOT send content field at all during tool call phase.

### 5. Transition Detection

**To detect content phase is complete:**

```python
if delta.get("tool_calls") and len(delta["tool_calls"]) > 0:
    # Content phase is complete (if there was one)
    # Tool call phase has started
```

**However**, better approach is:

```python
# Don't try to detect "content complete" separately
# Just track what's in each chunk and aggregate accordingly
# Use finish_reason as the authoritative completion signal
```

## Implications for Design

### Current Design Assumption (WRONG)

From `simplified_aggregation_design.md`:

```python
# Single "content" object and multiple "tool_call" objects
state.objects["content_msg"] = ResponseObject(type="content", ...)
state.objects[tc_id] = ResponseObject(type="tool_call", ...)
```

**Problem**: This implies they're independent objects that can be updated in any order.

### Correct Mental Model

Response is a **sequence of blocks**:

```python
@dataclass
class StreamBlock:
    """A block in the streaming response sequence."""
    type: Literal["content", "tool_call"]
    id: str  # Generated or from tool_call.id
    aggregator: Aggregator
    is_complete: bool  # True when we've moved to next block or got finish_reason

@dataclass
class StreamState:
    """Sequential structure of a streaming response."""
    blocks: list[StreamBlock]  # Sequential order matters
    current_block_index: int
    finish_reason: str | None
```

### Alternative: Keep It Simple

Actually, the current `StreamChunkAggregator` handles this correctly by:
1. Accumulating ALL content in `content_parts`
2. Accumulating ALL tool calls in `tool_calls` (indexed by id)
3. Not caring about the order or transition

**Recommendation**: Don't add block-level tracking unless you need it. The aggregator already works.

## Testing Data

Saved chunk sequences (as JSON):
- `/tmp/anthropic_multiple_tools_chunks.json` - 4 tool calls with brief thinking
- `/tmp/gpt_multiple_tools_chunks.json` - 4 tool calls, no content
- `/tmp/anthropic_extended_thinking_chunks.json` - 98 content chunks, then 2 tool calls
- `/tmp/no_tools_used_chunks.json` - Content only, no tools

Use these for unit testing aggregation logic.

## Open Questions

1. **Can content appear AFTER tool calls?**
   - Haven't observed this yet
   - Seems unlikely based on OpenAI/Anthropic APIs
   - Would break the sequential model

2. **Can tool calls interleave?**
   - No - they stream sequentially
   - Each completes before the next starts

3. **Other block types?**
   - Claude has "thinking" blocks in extended thinking mode
   - Not yet exposed in OpenAI-compatible streaming format
   - Future: citations, images, etc.

4. **Can same tool be called multiple times?**
   - Yes - each gets distinct `id` and `index`
   - Example: `get_weather` called for Tokyo (idx=0) and London (idx=2)

## Recommendations

### For Aggregation Infrastructure

1. **Keep current `StreamChunkAggregator`** - it works correctly
2. **Don't add complex block tracking** unless needed
3. **Use `finish_reason` as authoritative completion**
4. **Trust `index` as unique identifier** per tool call (with `id` as backup)

### For Policy Logic

1. **Don't assume single content or single tool call**
2. **Process tool calls as they complete** (sequentially)
3. **Buffer by tool call index** if you need to judge before forwarding
4. **Content is safe to forward immediately** (no judgement needed)

### For Future Extensions

1. **Design for sequential blocks**, not flat object map
2. **Prepare for new block types** (thinking, citations, etc.)
3. **Consider block-level callbacks** rather than chunk-level
4. **Allow policies to handle arbitrary block types**

## Code Examples

### Detecting Tool Call Completion

Since tool calls stream sequentially, a tool call is complete when:
1. Next tool call starts (different `index`), OR
2. `finish_reason` is set

```python
class ToolCallTracker:
    def __init__(self):
        self.current_index = None
        self.completed_indices = set()

    def process_chunk(self, delta):
        tool_calls = delta.get("tool_calls", [])

        for tc in tool_calls:
            tc_index = tc.get("index")

            # New tool call started?
            if tc_index != self.current_index:
                if self.current_index is not None:
                    # Previous tool call is complete
                    yield ("tool_call_complete", self.current_index)
                    self.completed_indices.add(self.current_index)
                self.current_index = tc_index
```

### Simple Aggregation (Current Approach)

```python
# This already works correctly:
aggregator = StreamChunkAggregator()
for chunk in chunks:
    aggregator.capture_chunk(chunk)

# At any point:
content = aggregator.get_accumulated_content()
tool_calls = aggregator.get_tool_calls()
is_done = aggregator.finish_reason is not None
```

No need to track blocks or phases explicitly!
