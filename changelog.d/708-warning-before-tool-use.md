---
category: Fixes
---

**SimpleLLMPolicy: inject judge-unavailable warning before tool_use, not after** (#708)
  - Previously, when the judge was unreachable and `on_error="pass"`, the warning
    text block was appended after any emitted `tool_use`. On the next turn the
    Anthropic API rejected the conversation with
    `messages.X: tool_use ids were found without tool_result blocks immediately after`,
    bricking the session (`API Error: 400 due to tool use concurrency issues`).
  - The warning is now injected before the first `tool_use` so the assistant
    message ends with the `tool_use`, satisfying the Anthropic invariant. Applies
    to both streaming and non-streaming paths.
