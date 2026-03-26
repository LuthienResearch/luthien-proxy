---
category: Fixes
pr: 443
---

**Fix streaming protocol violation in SimpleLLMPolicy**: When tool_use blocks are blocked by the judge, emit an explanatory text block (e.g., `[Tool call `Bash` was blocked by policy]`) instead of an orphaned `content_block_stop` or empty response. This fixes the Anthropic streaming protocol violation and allows Claude Code to continue the conversation after a tool call is blocked.
