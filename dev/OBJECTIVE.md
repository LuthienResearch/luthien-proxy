# Objective: Fix orphaned tool_result after /compact

## Problem for Luthien users
Claude Code users get error after extended sessions: "unexpected tool_use_id found in tool_result blocks. Each tool_result block must have a corresponding tool_use block in the previous message."

This breaks Claude Code workflows, especially after using `/compact` which likely removes the tool_use but leaves the tool_result.

## Acceptance Criteria
- [x] Claude Code can use `/compact` without triggering orphaned tool_result errors
- [x] Add pruning for orphaned tool_results (opposite of PR #166's tool_call pruning)
- [x] Unit tests cover the new pruning logic (9 unit tests)
- [x] E2E tests for orphaned tool_result scenarios (4 tests)
- [x] Manual test: Claude Code through Luthien with /compact - verified working

## Technical Notes
- Error comes from Anthropic API rejecting malformed message history
- Related to PR #166 which handles the opposite direction (tool_calls without results)
- Bidirectional pruning now in `processor.py`

## Implementation Complete
- Added `_prune_orphaned_tool_results()` in processor.py (O(n) single-pass algorithm)
- Applied for both Anthropic and OpenAI format requests
- All dev_checks pass
