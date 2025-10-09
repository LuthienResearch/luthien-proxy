# Implementation Plan: Kill ConversationLoggingPolicy

## Analysis

**What ConversationLoggingPolicy currently does:**
1. Implements async_pre_call_hook, async_post_call_success_hook
2. Aggregates streaming chunks (_capture_stream_chunk, _emit_stream_summary)
3. Tracks tool call state across chunks (ToolCallState, tool_call_indexes)
4. Provides parsing helpers (_parse_tool_calls, _content_to_text, etc.)
5. Emits structured JSON logs via logger.info()
6. Calls _record_debug_event() to write to debug_logs

**What core infra already does:**
1. Records all hook payloads to debug_logs (original and result)
2. Builds conversation events with original vs final payloads
3. Stores events in database
4. Publishes events to Redis for live streaming

**The key insight:**
- Core infra already records everything we need
- ConversationLoggingPolicy's _record_turn() is redundant with core infra
- But ToolCallBufferPolicy NEEDS the chunk aggregation logic to do buffering
- And it NEEDS the parsing helpers to extract tool calls

## Proposed Solution

1. **Create `src/luthien_proxy/utils/conversation.py`** - parsing helpers
   - Move: _parse_tool_calls, _parse_legacy_function_call, _content_to_text
   - Move: _extract_call_id, _extract_trace_id, metadata/params lookups
   - These are pure utility functions, no state

2. **Create `src/luthien_proxy/utils/streaming.py`** - chunk aggregation
   - Move: ToolCallState dataclass
   - Move: _capture_stream_chunk logic (extracts role, content, tool calls from deltas)
   - Move: _resolve_tool_call_identifier
   - Create a StreamChunkAggregator class that policies can use

3. **Update ToolCallBufferPolicy**
   - Inherit from LuthienPolicy instead of ConversationLoggingPolicy
   - Use StreamChunkAggregator for chunk state tracking
   - Use conversation utils for parsing
   - Keep its own ToolCallBufferContext for buffering state

4. **Delete ConversationLoggingPolicy**
   - Remove the file
   - Remove imports

5. **Update tests**
   - Fix test_conversation_logging_policy.py (probably delete it)
   - Update tool_call_buffer tests if needed

## Questions for Jai

Wait, I should ask first before implementing:

1. Do we even need the conversation utils? Or should we just move them inline into the policies that use them?
2. Should streaming chunk aggregation live in a shared utility, or should each policy implement its own?
3. The structured JSON logging that ConversationLoggingPolicy does - do we want to preserve that somewhere, or is the debug_logs recording enough?
