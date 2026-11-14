---
category: Fixes
pr: 827
---

**SimpleLLM judge reviews thinking blocks**: `thinking` blocks are judged before they reach the client
  - a passed block keeps its signature, and its text is shown to the judges of later blocks
  - a block that doesn't pass is removed and recorded with a `policy.simple_llm.thinking_dropped` event and a marker in the response. Because a `tool_use` must follow its thinking when thinking is enabled, the tool calls of that turn are blocked too
  - with interleaved thinking, reasoning that upstream sent after a `tool_use` keeps its position among the tool calls, and never ends up after the last one
  - the judge prompt explains what a thinking block is and that only reasoning which itself breaks the instructions should be replaced
  - **latency**: each thinking block is held until it is complete and judged, so thinking no longer streams live and the first byte waits for a judge call. Set `judge_thinking: false` in the policy config to stream thinking without judging it (reasoning that comes after a tool call still waits for the tool decisions, to keep its order). The built-in presets set it to false, since their rules are about visible text and tool calls
