# Thinking Blocks Fix - Debug Cycles Log

## Summary
Fixing streaming thinking blocks (#129) turned out to require **5 cycles** of fixes across multiple layers. Each fix revealed the next validation point or timing issue.

**Total cycles: 5** (as of 2026-01-24)

---

## Cycle 1: Streaming Assembler (Core Fix)
**Problem**: Streaming responses with thinking enabled didn't work - thinking blocks weren't recognized.

**Root Cause**: `anthropic_sse_assembler.py` only handled text and tool_calls, not thinking content.

**Fix**:
- Added handling for `delta.reasoning_content` → `thinking_delta` events
- Added handling for `delta.thinking_blocks` → `signature_delta` events
- Added `current_block_type` tracking for thinking→text transitions
- Added support for `redacted_thinking` blocks

**Files**: `src/luthien_proxy/streaming/client_formatter/anthropic_sse_assembler.py`

**Tests**: 14 new unit tests added

**Result**: Single-turn streaming with thinking works ✅

---

## Cycle 2: Conversation History (Multi-turn)
**Problem**: Multi-turn conversations with thinking enabled failed with 500 error from Anthropic API.

**Error**: `"messages.1.content.0.type: Expected 'thinking' or 'redacted_thinking', but found 'text'"`

**Root Cause**: `anthropic_to_openai_request()` in `llm_format_utils.py` was silently dropping `thinking` and `redacted_thinking` blocks from message history during format conversion.

**Fix**:
- Added handling for `thinking` and `redacted_thinking` block types
- Preserve thinking blocks in content array for passthrough to Anthropic
- Handle both text-only messages and tool_use messages with thinking

**Files**: `src/luthien_proxy/llm/llm_format_utils.py`

**Result**: Format conversion preserves thinking blocks ✅

---

## Cycle 3: Pydantic Request Validation
**Problem**: Multi-turn conversations now failed with 400 validation error from the proxy itself.

**Error**: `"messages.2.AssistantMessage.content: Input should be a valid string"`

**Root Cause**: The proxy's Pydantic models for request validation didn't support thinking blocks:
1. `AnthropicContentBlock` union didn't include thinking types
2. `AssistantMessage.content` only allowed `str | None`, not lists

**Fix**:
- Added `AnthropicThinkingBlock` and `AnthropicRedactedThinkingBlock` TypedDict classes
- Updated `AnthropicContentBlock` union to include thinking types
- Updated `AssistantMessage.content` to allow `str | list | None` for passthrough

**Files**:
- `src/luthien_proxy/llm/types/anthropic.py`
- `src/luthien_proxy/llm/types/openai.py`

**Result**: Validation passes ✅

---

## Cycle 4: Invalid Signature Error
**Problem**: Multi-turn still failing with `"Invalid 'signature' in 'thinking' block"`

**Root Cause**: Signature was being emitted to a NEW thinking block (index 2) instead of the ORIGINAL thinking block (index 0). LiteLLM sends signatures AFTER text content starts.

**Fix**:
- Track `last_thinking_block_index` to know where to send signatures
- Route `signature_delta` to the original thinking block regardless of current block

**Files**: `src/luthien_proxy/streaming/client_formatter/anthropic_sse_assembler.py`

**Result**: Signature goes to correct block, but still out of order ⚠️

---

## Cycle 5: Signature After Block Close
**Problem**: Signature was going to correct index, but AFTER the thinking block was closed. Anthropic requires signature BEFORE `content_block_stop`.

**Root Cause**: We were closing the thinking block when text started, but signature arrives later.

**Fix**:
- Add `thinking_block_needs_close` flag
- Delay `content_block_stop` for thinking blocks until signature arrives
- Emit signature → then emit pending stop

**Files**: `src/luthien_proxy/streaming/client_formatter/anthropic_sse_assembler.py`

**Result**: Correct event ordering - signature before close ✅

---

## Lessons Learned

1. **Format conversion is lossy** - When converting between API formats, it's easy to drop provider-specific features like thinking blocks.

2. **Validation happens at multiple layers** - Even after fixing the core logic, validation models at entry points can reject valid data.

3. **E2E testing is essential** - Unit tests passed at each stage, but real Claude Code usage revealed the next layer of issues.

4. **Anthropic's thinking feature has strict requirements** - When thinking is enabled, ALL previous assistant messages must include thinking blocks.

---

## Files Modified (Total)

1. `src/luthien_proxy/streaming/client_formatter/anthropic_sse_assembler.py` - Streaming thinking blocks + signature timing
2. `src/luthien_proxy/llm/llm_format_utils.py` - Preserve thinking in format conversion
3. `src/luthien_proxy/llm/types/anthropic.py` - Add thinking block types
4. `src/luthien_proxy/llm/types/openai.py` - Allow list content in AssistantMessage
5. `tests/unit_tests/streaming/client_formatter/test_anthropic_sse_assembler.py` - New + updated tests
6. `tests/unit_tests/streaming/client_formatter/test_anthropic.py` - New tests

## Key Insight

LiteLLM delivers thinking signatures OUT OF ORDER (after text content starts). The fix requires:
1. Delaying `content_block_stop` for thinking blocks
2. Tracking which block needs the signature
3. Emitting signature → then stop, in correct order

---

## Pressure Testing (2026-01-24)

### Automated Tests Added
- [x] Tool calls + thinking transition (unit test) - **PASS**
- [x] Fixed existing test for delayed block close - **PASS**
- [x] All 852 tests pass

### Manual Testing Required (Scott)

| Scenario | Risk | How to Test |
|----------|------|-------------|
| Tool calls + thinking | HIGH | Ask Claude Code to "read README.md and summarize it" with thinking enabled |
| Images + thinking | MEDIUM | Send image + "analyze this" with thinking enabled (Issue #108 exists) |
| Long thinking content | LOW | Ask complex reasoning question |
| Rapid multi-turn | LOW | Fast back-and-forth with thinking |

### Results
- Tool call + thinking: Unit test added, covers block transition ✅
- Test suite updated for delayed block close behavior ✅
