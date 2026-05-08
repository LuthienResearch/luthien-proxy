---
category: Fixes
pr: 721
---

**ToolCallJudgePolicy streaming stop_reason**: When every `tool_use` block in a streaming response was blocked by the judge, the terminal `message_delta` still reported `stop_reason="tool_use"`, causing Claude Code (and other Anthropic SDK consumers) to abort with `"The model's tool call could not be parsed (retry also failed)."`. The streaming path now rewrites `stop_reason` to `"end_turn"`, mirroring the existing behavior of the non-streaming path.
