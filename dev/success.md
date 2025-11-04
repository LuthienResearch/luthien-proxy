# Development Successes

A log of successfully completed debugging and implementation tasks.

## 2025-11-04: Fixed ToolCallJudgePolicy Streaming

**Problem**: Streaming broke with ToolCallJudgePolicy - no chunks reached client, causing "message_stop before message_start" errors. Blocked tool calls showed no response.

**Root causes**:
1. Policy didn't forward content chunks (missing `on_content_delta`)
2. `create_text_chunk()` used dict instead of `Delta` object, breaking SSE assembler
3. Single chunk with both content + finish_reason only processed content, missing close events
4. `create_text_chunk()` used `Choices` instead of `StreamingChoices` (found via unit tests)

**Fix**:
- Added `on_content_delta` to forward content chunks to egress
- Updated `create_text_chunk()` to use `Delta(content=text)` instead of dict
- Updated `create_text_chunk()` to use `StreamingChoices` instead of `Choices`
- Split blocked message into two chunks: content chunk + finish chunk

**Files modified**:
- `src/luthien_proxy/v2/policies/tool_call_judge_policy.py` (added on_content_delta, fixed two-chunk pattern)
- `src/luthien_proxy/v2/policies/utils.py` (fixed Delta object type and Choices type)

**Tests added**:
- `tests/unit_tests/v2/policies/test_tool_call_judge_policy.py` - 8 regression tests covering all 4 bugs

**Result**: Streaming works with complete Anthropic SSE event sequences. Blocked tool calls display explanation messages. All bugs would have been caught by the new unit tests.
