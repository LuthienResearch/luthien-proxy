# Objective

Add unit tests for ToolCallJudgePolicy and extract shared streaming event test helpers.

## Description

ToolCallJudgePolicy has zero direct unit test coverage. The utility functions (tool_call_judge_utils.py) are well-tested, but the policy's Anthropic hook methods — streaming event handling, tool buffering, block replacement, judge failure behavior, and stop_reason correction — are untested.

Additionally, the streaming event builder helpers in test_simple_llm_policy.py are protocol-level primitives that should be shared across policy tests rather than duplicated.

## Approach

1. Extract event builders (_text_start, _tool_start, _block_stop, _message_delta, _event_types, etc.) from test_simple_llm_policy.py into a shared anthropic_event_builders.py module
2. Update test_simple_llm_policy.py to import from the shared module, verify tests pass
3. Create test_tool_call_judge_policy.py covering:
   - Streaming: tool pass-through, tool blocked (replaced with text), text pass-through, judge failure (fail-secure)
   - Non-streaming: tool pass/block, all-tools-blocked stop_reason correction, empty content, mixed content
   - State cleanup via on_anthropic_streaming_policy_complete
   - Blocked message template formatting
4. Mock _evaluate_and_maybe_block_anthropic to control judge decisions (utils already tested separately)

## Test Strategy

- Unit tests only — no e2e needed (existing mock e2e tests cover integration)
- Mock judge calls at the policy method level
- Use shared event builders for protocol event construction

## Acceptance Criteria

- [ ] Shared event builders extracted and test_simple_llm_policy.py still passes
- [ ] test_tool_call_judge_policy.py covers all Anthropic hook methods
- [ ] dev_checks passes
- [ ] No orphaned stop events in any test scenario

## Tracking

- Trello: none
- Branch: worktree-the-4th-thing
- PR: TBD
