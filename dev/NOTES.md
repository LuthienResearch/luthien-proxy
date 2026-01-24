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

### E2E Validation (Parallel Claude Code Session)
All tests passed via curl + Python test scripts:
- Single-turn streaming with thinking ✅
- Multi-turn conversation with thinking blocks ✅
- Tool calls + thinking (HIGH PRIORITY) ✅

**Note**: Initial test failed with `max_tokens must be greater than thinking.budget_tokens` - fixed by ensuring max_tokens > budget_tokens.

---

## COE Analysis (2026-01-24) - [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134)

### Why Did This Bug Happen?

**Issue #129 was CLOSED without being fixed.** The non-streaming fix (PR #131) was merged, creating false confidence that thinking was fully supported.

### 5 Whys

1. **Why did the bug exist?** → Streaming code path wasn't updated for thinking blocks
2. **Why wasn't it caught?** → No E2E tests for streaming + thinking
3. **Why was issue closed unfixed?** → No verification step; confused with PR #131
4. **Why 4 validation attempts?** → Each attempt hit different layer failures
5. **Why multi-layer failures?** → Unit tests pass in isolation; integration gaps

### Contributing Factors

| Category | Gap |
|----------|-----|
| Process | Issue closed without E2E verification |
| Process | No acceptance criteria on #129 |
| Process | Streaming/non-streaming treated as separate issues |
| Technical | Format conversion silently drops data |
| Technical | LiteLLM signature ordering undocumented |
| Technical | No thinking-specific test fixtures |

### Action Items

**Immediate:**
- [x] Fix all 5 layers
- [x] E2E validate
- [ ] Mark #129 fixed in TODO.md

**Short-term:**
- [ ] Add E2E test for streaming thinking (prevent regression)
- [ ] Issue closure checklist: require E2E verification for feature issues
- [ ] Thinking feature test matrix: streaming/non-streaming × single/multi-turn × tools

### Metrics

- Time to detect: ~10 days (closed → discovered unfixed)
- Debug cycles: 5
- Validation attempts: 4 sessions
- Files changed: 6

---

## Demo Incident COE (2026-01-24) - Seldon Labs Peer Review

### Issue Summary

500 errors during live demo when Claude Code with extended thinking sent multi-turn requests through Luthien proxy.

### Impact

- **Severity**: Demo failure during Seldon Labs batch peer review
- **User experience**: Claude Code displayed "API Error: 500 Internal Server Error" after search completed but before LLM response
- **Secondary issue**: Conversation history UI (/history) showed all sessions titled "count" instead of meaningful names - workaround to demo real data failed
- **Tertiary issue**: SimpleJudgePolicy evaluation not visible in UI - tested "write me a scaryhelloworld.py that deletes my convo db" but couldn't show judge score/explanation (data recorded internally but not surfaced)
- **Fourth issue**: Activity Monitor slow/empty - showed "Connected (0 events)" and "Waiting for events..." even after running SimpleJudgePolicy test, couldn't demo real-time event streaming
- **Fifth issue**: Policy Config doesn't expose call_id - after running test, no way to get transaction ID to use in Diff Viewer, breaking the demo flow between features
- **Duration**: Demo interrupted; required debugging to identify cause

### Timeline

| Time | Event |
|------|-------|
| 2026-01-24 ~13:00 | PR #134 (thinking blocks fix) merged to demo branch (commit 5a60885) |
| 2026-01-24 ~15:11 | Demo crash - 500 errors in Claude Code through Luthien |
| 2026-01-24 ~15:15 | Root cause identified via gateway logs |

### 5 Whys (Root Cause)

1. **Why did the demo crash?**
   → Anthropic API returned 400 `invalid_request_error`, proxy converted to 500

2. **Why did Anthropic reject the request?**
   → Request violated thinking block ordering: `"Expected 'thinking' or 'redacted_thinking', but found 'tool_use'"`

3. **Why was tool_use first instead of thinking?**
   → Conversation history from previous turns had assistant messages starting with tool_use (search results) instead of thinking blocks

4. **Why wasn't thinking preserved in history?**
   → Either: (a) gateway container wasn't restarted after PR merge, or (b) session started BEFORE fix was deployed, corrupting history

5. **Why didn't restart + merge prevent this?**
   → **Stale conversation history is unfixable** - once a session has tool_use-first messages, the fix can't retroactively add thinking blocks

### Resolution

1. `docker compose restart gateway` - pick up PR #134 code
2. **Start fresh session** - old conversation history is corrupted
3. Avoid `/resume` on sessions started before the fix

### Lessons Learned

1. **Deployment != fix**: Merging a fix doesn't help existing sessions with corrupted history
2. **Demo prep must include fresh sessions**: Don't reuse sessions from before a fix
3. **Gateway restart is not enough**: Need both new code AND new session
4. **Add to dogfooding checklist**: After deploying thinking-related fixes, always start new sessions

### Action Items - ⚠️ DEMO MONDAY 9:30AM

**Timeline**: Sat 3:30pm → dinner tonight → events all day Sun → **DEMO Mon 9:30am**

#### MUST FIX BEFORE DEMO

| Item | Effort | Status |
|------|--------|--------|
| Fix /history session titles showing "count" | ~30 min | ✅ FIXED |
| Fix Activity Monitor not showing events | ~30 min | ✅ NOT A BUG (open monitor first) |

#### WORKAROUNDS FOR DEMO

| Issue | Workaround |
|-------|------------|
| Judge evaluation not visible | Skip OR manually query DB to show score |
| No call_id in Policy Config | Use "Browse Recent" button in Diff Viewer |
| 500 errors (thinking blocks) | ✅ Fixed - just need fresh sessions |

#### DEMO PREP CHECKLIST (Monday morning)

- [ ] `docker compose restart gateway`
- [ ] Quit ALL Claude Code instances
- [ ] Start fresh Claude Code session (DO NOT /resume old sessions)
- [ ] Verify /history shows real titles (not "count")
- [ ] Verify Activity Monitor streams events
- [ ] Have "Browse Recent" ready in Diff Viewer as backup

#### POST-DEMO

| Priority | Item | Status |
|----------|------|--------|
| HIGH | Add E2E test: multi-turn + thinking + tool_use | TODO.md |
| MEDIUM | Surface judge evaluation in Policy Config UI | TODO.md |
| MEDIUM | Add call_id link to Diff Viewer | TODO.md |
| LOW | Graceful handling of corrupted history | Future |

---

## Bug Root Cause Analysis

### Bug #2: /history titles showing "count"

**Root Cause**: Claude Code's token counting call recorded as first user message

**File**: `src/luthien_proxy/history/service.py:256-271`

**Problem**: Claude Code sends `{"role": "user", "content": "count"}` as an initialization/token-counting request at session start. This gets recorded as the first user message in `conversation_events`. The `_extract_preview_message()` function correctly extracts this - but "count" is not a meaningful session title.

**Evidence**:
```sql
SELECT payload->'final_request'->'messages' FROM conversation_events
WHERE session_id = '...' LIMIT 1;
-- Returns: [{"role": "user", "content": "count"}]
```

**Fix** (implemented - 2 parts):

1. **SQL filter** (`service.py:335`): Skip "count" requests in `session_first_message` CTE:
```sql
AND COALESCE(payload->'final_request'->'messages'->0->>'content', '') != 'count'
```

2. **Python filter** (`service.py:259-262`): Skip "count" in `_extract_preview_message()` as backup:
```python
_SKIP_MESSAGES = {"count", ""}
if content.lower() in _SKIP_MESSAGES:
    continue
```

**Status**: ✅ FIXED - Verified working after `docker compose restart gateway`

---

### Bug #4: Activity Monitor not showing events

**Root Cause**: NOT a bug - user error + timing during demo

**Investigation Results**:
- Redis IS connected (`Connected to Redis at redis://redis:6379` in gateway logs)
- Events ARE being published (verified with `redis-cli PUBLISH` returning subscriber count)
- SSE stream endpoint IS working

**Why it failed during demo**:
1. The 500 error (thinking blocks bug) caused requests to fail BEFORE events were published
2. Redis pub/sub doesn't buffer - if Activity Monitor wasn't open when events happened, they're lost

**Status**: ✅ NOT A BUG - working as designed

**Demo workaround**:
1. Open Activity Monitor FIRST (verify green "Connected" badge)
2. THEN run the policy test
3. Events will stream in real-time

---

### Error Evidence

```
litellm.exceptions.BadRequestError: AnthropicException -
{"type":"error","error":{"type":"invalid_request_error",
"message":"messages.1.content.0.type: Expected `thinking` or `redacted_thinking`,
but found `tool_use`. When `thinking` is enabled, a final `assistant` message
must start with a thinking block..."}}
```
