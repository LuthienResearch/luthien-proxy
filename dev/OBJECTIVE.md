# Objective: Fix Streaming Thinking Blocks (#129)

**Status**: Testing additional fix for conversation history

## Context
- Monday 10am demo to Seldon Labs - working demo is critical
- Issue #129 was closed by Jai but NOT actually fixed
- Non-streaming fix was in PR #131, but streaming code was untouched

## What Was Done
- [x] Validated issue #129 was NOT fixed (streaming assembler had no thinking handling)
- [x] Researched Anthropic SSE format for thinking blocks
- [x] Researched LiteLLM streaming format (reasoning_content, thinking_blocks)
- [x] Implemented fix in `anthropic_sse_assembler.py`
- [x] Added 14 unit tests for thinking block handling
- [x] All 851 tests pass
- [x] Created draft PR #134
- [x] **E2E verified** with claude-3-7-sonnet-20250219 + thinking enabled (single turn)

## Related Bug Found During Testing
**Multi-turn conversations with thinking fail** - Anthropic API requires thinking blocks
in conversation history when thinking is enabled. The proxy was dropping `thinking` and
`redacted_thinking` blocks from message history during Anthropic→OpenAI format conversion.

**Fix applied to `llm_format_utils.py`:**
- Added handling for `thinking` and `redacted_thinking` block types
- Preserve thinking blocks in content array for passthrough to Anthropic
- Handle both text-only messages and tool_use messages with thinking

## Remaining
- [x] E2E test multi-turn conversation with thinking ✅ Verified working!
- [x] Pressure testing before Monday demo:
  - [x] Tool calls + thinking (unit test added) ✅
  - [ ] Long thinking content - manual test recommended
  - [ ] Rapid multi-turn - manual test recommended
  - [ ] Images + thinking - manual test recommended (Issue #108 exists)
- [ ] Get PR reviewed and merged

## PR
https://github.com/LuthienResearch/luthien-proxy/pull/134
