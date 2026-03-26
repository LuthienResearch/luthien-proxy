# Objective

Remove dead code from ToolCallJudgePolicy.

## Description

`_call_judge_with_failsafe()` (lines ~444-471) and `_create_judge_failure_message()` (lines ~473-479) are never called. The active code path uses `_evaluate_and_maybe_block_anthropic()` which calls `call_judge()` directly and handles exceptions inline. These methods are leftover from a previous refactor.

## Approach

- Delete both dead methods
- Verify no references exist anywhere in the codebase
- Run dev_checks to confirm nothing breaks

## Test Strategy

- Unit tests: existing tests should continue to pass (no new tests needed — we're only deleting dead code)
- dev_checks: must pass clean

## Acceptance Criteria

- [ ] `_call_judge_with_failsafe()` deleted
- [ ] `_create_judge_failure_message()` deleted
- [ ] No references to deleted methods remain
- [ ] dev_checks passes

## Tracking

- Trello: none
- Branch: worktree-calm-hopping-perlis
- PR: TBD
