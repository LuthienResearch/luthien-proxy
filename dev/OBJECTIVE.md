# Objective

Fix `ToolCallJudgePolicy` streaming path so the terminal `message_delta` reports `stop_reason="end_turn"` (not `"tool_use"`) when every `tool_use` block was blocked, matching the non-streaming path's existing behavior.

## Description

`ToolCallJudgePolicy` substitutes blocked `tool_use` blocks with text blocks in both the non-streaming and streaming paths. The non-streaming path also rewrites `stop_reason` from `"tool_use"` → `"end_turn"` when no `tool_use` survives (`tool_call_judge_policy.py:250-252`). The streaming path does not — there is no handler for `RawMessageDeltaEvent`, so it passes through with the upstream's original `stop_reason="tool_use"`. Claude Code (and any well-behaved Anthropic SDK consumer) reads `stop_reason: tool_use`, expects a `tool_use` content block to invoke, finds only text, and gives up with `"The model's tool call could not be parsed (retry also failed)."`.

The exact fix pattern already exists in `simple_llm_policy.py:317-318` and `simple_llm_policy.py:432-462` (`_handle_message_delta`). Mirror it.

## Approach

1. Add `had_allowed_tool_use: bool = False` to `_ToolCallJudgeAnthropicState`. Set `True` in the "allowed" branch of `_handle_anthropic_content_block_stop`.
2. Add `RawMessageDeltaEvent` import to `tool_call_judge_policy.py`.
3. Add a `RawMessageDeltaEvent` branch in `on_anthropic_stream_event` that calls a new `_handle_anthropic_message_delta`.
4. Implement `_handle_anthropic_message_delta`: rewrite `stop_reason` `"tool_use"` → `"end_turn"` iff `state.blocked_blocks` is non-empty AND `state.had_allowed_tool_use` is `False`. Use `RawMessageDeltaEvent.model_construct(...)` and `event.delta.model_copy(update={...})`.

## Hot-path sequence

```
# Upstream (Anthropic):
content_block_start(index=0, content_block=ToolUseBlock(id="toolu_1", name="Read"))
content_block_delta(index=0, delta=InputJSONDelta(partial_json='{"file":"x"}'))
content_block_stop(index=0)
message_delta(delta=Delta(stop_reason="tool_use", ...), usage=...)
message_stop()

# Policy emits downstream (after fix):
content_block_start(index=0, content_block=TextBlock(type="text", text=""))
content_block_delta(index=0, delta=TextDelta(text="⛔ TEST_BLOCK: Tool 'Read' rejected by judge"))
content_block_stop(index=0)
message_delta(delta=Delta(stop_reason="end_turn", ...), usage=...)   # NEW: rewritten
message_stop()

# State after content_block_stop(0):
#   buffered_tool_uses={}, blocked_blocks={0}, had_allowed_tool_use=False
# At message_delta:
#   condition met → rewrite stop_reason → emit
```

## External Contracts

- **Anthropic streaming protocol**: a `message_delta` whose `delta.stop_reason="tool_use"` MUST be accompanied by at least one `tool_use` content block in the emitted stream. This change preserves that invariant.
- **`AnthropicHookPolicy.on_anthropic_stream_event`**: returns `list[MessageStreamEvent]`. New branch returns `[event]` (unchanged) or `[rewritten_event]` (single, same type). No protocol-ordering implications.
- **`on_anthropic_response`** (non-streaming): unchanged — already correct.

## Assumptions

- Upstream Anthropic emits exactly one terminal `RawMessageDeltaEvent` per request.
- For mixed (some blocked, some allowed) tool sets, keeping `stop_reason="tool_use"` is correct — surviving `tool_use` blocks still invoke. Verified against `simple_llm_policy.py:452-454`.
- `event.delta` is a `MessageDelta` model with `model_copy(update=...)`; `RawMessageDeltaEvent` supports `model_construct`. Verified at `simple_llm_policy.py:455-459`.

## Test Strategy

- **Failing test**: `tests/luthien_proxy/unit_tests/policies/test_tool_call_judge_policy.py::TestStreamingStopReasonCorrection::test_stop_reason_corrected_after_tool_blocked` — drives blocked tool flow + `message_delta("tool_use")`, asserts `delta.stop_reason == "end_turn"`.
- **Additional unit tests** (same class): allowed → unchanged; mixed → unchanged; no-tools → unchanged; non-`"tool_use"` stop_reason → unchanged.
- **Mock e2e regression**: streaming `/v1/messages` → assert terminal SSE `message_delta.stop_reason == "end_turn"` and that the blocked-message text appears.
- **Existing e2e** `test_claude_code_with_tool_judge_low_threshold`: validates the user-visible bug; should pass after fix.
- `scripts/dev_checks.sh` passes.

## Acceptance Criteria

- [ ] New `TestStreamingStopReasonCorrection` class with the cases above; first test fails on `main`, all pass after fix.
- [ ] New mock-e2e regression for SSE `stop_reason`.
- [ ] `had_allowed_tool_use` field added; `_handle_anthropic_message_delta` mirrors `simple_llm_policy._handle_message_delta`.
- [ ] Stale comment in `test_tool_call_judge_policy.py` (about "ToolCallJudgePolicy doesn't handle message_delta events") removed.
- [ ] `scripts/dev_checks.sh` passes.

## Tracking

- Trello: https://trello.com/c/zjzq6aP5
- Branch: `fix/tool-judge-streaming-stop-reason`
- PR: https://github.com/LuthienResearch/luthien-proxy/pull/721
