# Streaming Chunk Patterns Across Providers

**Date**: 2025-10-23
**Models Tested**: GPT-5, GPT-4o-mini, Claude Sonnet 4

## Key Findings

### Universal Pattern

Both OpenAI and Anthropic follow the **same streaming pattern**:

1. **Content streams incrementally** - text arrives in small chunks
2. **Tool calls stream incrementally** - name arrives first, then arguments character-by-character
3. **finish_reason arrives separately** - in a dedicated chunk after all data

### Chunk Structure

Every chunk has:
```json
{
  "id": "chatcmpl-...",
  "created": 1761262910,
  "model": "...",
  "object": "chat.completion.chunk",
  "choices": [{
    "index": 0,
    "delta": {
      "role": "assistant",        // Only in first chunk
      "content": "text...",        // Incremental text
      "tool_calls": [{...}]        // Incremental tool call deltas
    },
    "finish_reason": null         // null until final chunk
  }]
}
```

Final chunk:
```json
{
  "choices": [{
    "delta": {},                   // Empty delta
    "finish_reason": "stop"|"tool_calls"
  }]
}
```

### Content Streaming

**Pattern**: Incremental text deltas
- Chunk 1: `role: "assistant"`, `content: "Hello"`
- Chunk 2: `content: ", how"`
- Chunk 3: `content: " are"`
- Chunk 4: `content: " you?"`
- Chunk 5: `finish_reason: "stop"`

### Tool Call Streaming

**Pattern**: Name first, then incremental arguments

**GPT-4o-mini / GPT-5**:
- Chunk 1: `role: "assistant"`, `tool_calls: [{name: "get_weather", arguments: ""}]`
- Chunk 2: `tool_calls: [{arguments: "{\""}]`
- Chunk 3: `tool_calls: [{arguments: "location"}]`
- Chunk 4: `tool_calls: [{arguments: "\":\""}]`
- Chunk 5: `tool_calls: [{arguments: "Tokyo\""}]`
- Chunk 6: `tool_calls: [{arguments: "}"}]`
- Chunk 7: `finish_reason: "tool_calls"`

**Claude Sonnet 4**:
- Chunk 1-3: Content chunks (pre-tool-call text)
- Chunk 4: `tool_calls: [{id: "toolu_...", name: "get_weather", arguments: ""}]`
- Chunk 5: `tool_calls: [{arguments: ""}]`  (empty)
- Chunk 6: `tool_calls: [{arguments: "{\"locatio"}]`
- Chunk 7: `tool_calls: [{arguments: "n\": \""}]`
- Chunk 8+: More argument chunks
- Final: `finish_reason: "tool_calls"`

**Key difference**: Claude may send content BEFORE tool calls in the same response.

### Multiple Tool Calls

When calling multiple tools, all tool calls can arrive in a **single chunk** (non-streaming):
```json
"tool_calls": [
  {"id": "call_1", "function": {"name": "get_weather", "arguments": "{\"location\":\"NYC\"}"}, "index": 0},
  {"id": "call_2", "function": {"name": "get_weather", "arguments": "{\"location\":\"SF\"}"}, "index": 0}
]
```

**Note**: Both have `index: 0` - the `id` field is what distinguishes them, not `index`.

## Implications for Aggregation

1. **Content and tool calls are separate streams** - they don't interleave at the delta level
2. **Arguments ALWAYS stream incrementally** - never assume complete-in-one-chunk
3. **finish_reason is the only reliable completion signal** - don't try to detect completion from delta structure
4. **index field is NOT unique** - use `id` to track tool calls
5. **Providers are consistent** - one aggregation strategy works for all

## Simplification Opportunities

Based on these patterns, we can:

1. **Remove tool-call-specific logic** - content and tool calls aggregate the same way (incremental deltas)
2. **Use finish_reason as universal completion signal** - no need for heuristics
3. **Single aggregator type** - one `StreamAggregator` works for all response types
4. **Keying by (type, id) not (type, index)** - index is not unique

## Sample Files

- `gpt_streaming_chunks.txt` - Full GPT-5 and GPT-4o-mini streaming examples
- `anthropic_streaming_chunks.txt` - Full Claude Sonnet 4 streaming examples
