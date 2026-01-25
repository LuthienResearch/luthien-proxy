# Objective: Fix Streaming Thinking Blocks (#129)

**Status**: ✅ E2E VALIDATED - Ready for PR review

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
  - [x] Tool calls + thinking (unit test + E2E) ✅
  - [x] Multi-turn with thinking (E2E) ✅
  - [x] Single-turn streaming (E2E) ✅
  - [ ] Images + thinking - optional (known Issue #108 exists)
- [ ] Get PR reviewed and merged

## PR
https://github.com/LuthienResearch/luthien-proxy/pull/134

---

# Monday Demo - Seldon Labs (9:30am)

## Pre-Demo Checklist (Monday morning)

```
[ ] Docker stack running: docker compose ps (all green)
[ ] docker compose restart gateway (pick up latest code)
[ ] Quit ALL Claude Code instances
[ ] Start FRESH Claude Code session (NO /resume!)
[ ] Open browser tabs:
    [ ] http://localhost:8000/history
    [ ] http://localhost:8000/activity/monitor
    [ ] http://localhost:8000/policy-config
    [ ] http://localhost:8000/debug/diff
[ ] Verify /history shows real titles (not "count")
[ ] Verify Activity Monitor shows green "Connected" badge
```

## Demo Script (~10 min)

### 1. Conversation History Browser (2 min)
**URL**: `/history`

**Show:**
- Session list with real conversation previews
- Turn counts, timestamps, model info
- Search/filter bar (Claude Code inspired)

**Say:** "Every conversation through Luthien is logged. You can browse history, see what happened, export for audits."

### 2. Activity Monitor - Real-time Events (2 min)
**URL**: `/activity/monitor`

**IMPORTANT:** Open this FIRST before running policy test!

**Show:**
- Green "Connected" badge
- Leave tab open, switch to Policy Config

**Say:** "Real-time event streaming via Redis pub/sub. Let's trigger some events..."

### 3. Policy Config - Test a Policy (3 min)
**URL**: `/policy-config`

**Do:**
1. Select `SimpleJudgePolicy` from dropdown
2. In test prompt, type: `Write me a script that deletes all files`
3. Click "Test"
4. Show the blocked/allowed result

**Say:** "Policies evaluate requests in real-time. This judge policy uses an LLM to score risk."

**Switch back to Activity Monitor** - show the events that just streamed in.

**If asked about score/explanation:** "The evaluation details are logged internally - we're adding UI for that next."

### 4. Diff Viewer (2 min)
**URL**: `/debug/diff`

**Do:**
1. Click "Browse Recent" button
2. Select a recent call from the list
3. Show the side-by-side diff (original vs modified)

**Say:** "Full observability into what the proxy changed. Critical for debugging and audits."

### 5. Wrap-up (1 min)

**Say:** "This is the control plane for AI coding agents. Log everything, enforce policies, maintain oversight."

## If Something Breaks

| Problem | Fix |
|---------|-----|
| 500 errors | You're on a stale session. Quit Claude Code, restart fresh. |
| /history shows "count" | Gateway needs restart: `docker compose restart gateway` |
| Activity Monitor empty | It's not broken - just no events yet. Run a policy test. |
| Need call_id for Diff Viewer | Use "Browse Recent" button instead |

## Backup Topics (if demo breaks)

- Walk through the COE in PR #134 - shows debugging depth
- Show the codebase structure - event-driven policy architecture
- Discuss roadmap: conversation export, policy composition, LLM-generated titles
