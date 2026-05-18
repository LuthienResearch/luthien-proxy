---
category: Fixes
pr: 754
---

**AnthropicMessageBuilder: stop_reason rewrite is now conservative; diagnostic upstream reasons preserved**
  - Previously the builder unconditionally rewrote `stop_reason` to
    `tool_use` or `end_turn` based on whether any tool was actually
    emitted. That clobbered legitimate upstream `max_tokens`,
    `stop_sequence`, `refusal`, and `pause_turn` values, masking
    information clients depend on (e.g. a `max_tokens` truncation
    looked like a normal `end_turn` to the client and never triggered
    continuation logic).
  - **Fix**: only rewrite when upstream is *known wrong* given what we
    emitted — specifically, `stop_reason == "tool_use"` with no tool
    on the wire becomes `end_turn`. Every other reason is preserved
    verbatim. Mirrors the conservative shape established in PR #721.
  - Affects all Anthropic streaming and non-streaming policies that
    route through `AnthropicMessageBuilder` (SimpleLLM, DogfoodSafety,
    ToolCallJudge).
