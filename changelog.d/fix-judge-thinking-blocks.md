---
category: Fixes
pr: 827
---

**SimpleLLM judge reviews thinking blocks**: `thinking` blocks are judged before they reach the client
  - **behavior change for existing configs**: `judge_thinking` defaults to `true`, so a SimpleLLM policy that doesn't set it starts judging `thinking` blocks after the upgrade. That means one more judge call per thinking block, thinking that no longer streams live, and a turn that loses its tool calls when its thinking is removed. Set `judge_thinking: false` to keep the old behavior. The built-in presets already set it to false
  - a passed block keeps its signature, and its text is shown to the judges of later blocks
  - a block that doesn't pass is removed and recorded with a `policy.simple_llm.thinking_dropped` event and a marker in the response. The tool calls of that turn are blocked too, since the reasoning that led to them did not pass
  - a reasoning block is never moved, because the API binds its signature to the content before it. With interleaved thinking, reasoning that upstream sent after a `tool_use` keeps its position among the tool calls. It is removed when it can't stay there: when no tool call follows it, when text or a marker goes before the tool calls, or when an earlier reasoning block of the turn was dropped
  - the judge prompt explains what a thinking block is and that only reasoning which itself breaks the instructions should be replaced
  - **latency**: each thinking block is held until it is complete and judged, so the first byte waits for a judge call. With `judge_thinking: false` thinking streams unjudged (reasoning that comes after a tool call still waits for the tool decisions, to keep its order). The presets set it to false because their rules are about visible text and tool calls
