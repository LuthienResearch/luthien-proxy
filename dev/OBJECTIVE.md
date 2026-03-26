# Objective

Fix streaming protocol violation in SimpleLLMPolicy where orphaned `content_block_stop` events are emitted for tool_use blocks whose `content_block_start` was suppressed.

## Description

When `on_error="block"` and a judge call fails on a tool_use block, `_handle_block_stop` emits `content_block_stop` for a block whose `content_block_start` was suppressed (line 294). This violates Anthropic's streaming protocol — a `content_block_stop` must always have a preceding `content_block_start`.

The bug is in the fallthrough path of `_handle_block_stop`: when the judge action is "block" (neither "pass" nor "replace"), the method returns `[event]` — emitting the stop event. For tool_use blocks, the start was suppressed at line 294, so this creates an orphaned stop.

## Approach

1. **Fix the bug**: In `_handle_block_stop`, when a tool_use block's judge action is "block", return `[]` instead of `[event]` to suppress the orphaned stop (since start was already suppressed).
   - Text blocks are unaffected — their start IS emitted, so the stop must be emitted too.

2. **Add unit tests**: Create `test_simple_llm_policy.py` covering streaming event handling:
   - Text block pass-through
   - Text block blocked (judge action = "block")
   - Tool block pass-through
   - Tool block blocked — the actual bug case (no orphaned stop)
   - Tool block replacement
   - Judge failure with on_error="pass" (warning injection)
   - Judge failure with on_error="block" (suppress tool block)
   - stop_reason correction after blocking tool_use
   - Non-streaming response pass-through and blocking

## Test Strategy

- Unit tests: Mock `_judge_block` to return controlled `JudgeAction` results. Test streaming event sequences for correctness (no orphaned stops, proper start/delta/stop triplets).
- E2E: Existing `test_mock_simple_llm_policy.py` tests cover the end-to-end behavior.

## Acceptance Criteria

- [ ] No orphaned `content_block_stop` events in streaming output when tool_use is blocked
- [ ] Unit tests for SimpleLLMPolicy streaming event handling
- [ ] Mock e2e tests `test_mock_simple_llm_policy.py` still pass
- [ ] dev_checks passes

## Tracking

- Trello: none
- Branch: worktree-agile-tickling-pebble
- PR: (filled later)
