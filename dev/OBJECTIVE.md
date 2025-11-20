# Objective: Fix ToolCallJudgePolicy Inheritance (#62)

## Goal
Fix `ToolCallJudgePolicy` to inherit from `BasePolicy` instead of `PolicyProtocol`, and prevent double token streaming bug.

## Changes Required
1. Change inheritance from `PolicyProtocol` to `BasePolicy`
2. Override `on_chunk_received()` to prevent duplicate token pushing

## Acceptance Criteria
- [ ] ToolCallJudgePolicy inherits from BasePolicy
- [ ] on_chunk_received() is overridden to prevent double-pushing
- [ ] Gateway starts successfully with ToolCallJudgePolicy configured
- [ ] Streaming responses have no duplicate tokens
- [ ] All tests pass

## Related
- Issue: #62
