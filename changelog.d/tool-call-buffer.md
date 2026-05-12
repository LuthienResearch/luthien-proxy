---
category: Refactors
pr: 748
---

**Extract `ToolCallStreamBuffer`**: policy-agnostic Anthropic streaming filter parameterized by a caller-supplied async transform closure. `DogfoodSafetyPolicy` and `ToolCallJudgePolicy` now define only a transform closure; per-request state, output indices, and `stop_reason` invariants are owned by the buffer. Replaces #728's per-event-helper extraction (which dropped the `message_delta` `stop_reason` rewrite). Tool-call decisions now run at `message_delta` with the full list of buffered tool calls, instead of per-block at `content_block_stop`.
