---
category: Fixes
pr: 719
---

**Fix safety-judge stream corruption**: `SimpleLLMPolicy` and `ToolCallJudgePolicy` no longer emit streaming events that violate the Anthropic block-index/stop-reason invariants when the judge blocks, replaces, or fails on a tool call. This prevented sessions from continuing after a judge failure on single or parallel tool_use responses (issue #708).
