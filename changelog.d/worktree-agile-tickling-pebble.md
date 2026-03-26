---
category: Fixes
pr: 443
---

**Fix streaming protocol violation in SimpleLLMPolicy**: Suppress orphaned `content_block_stop` events when tool_use blocks are blocked by the judge. Add unit tests for SimpleLLMPolicy streaming event handling.
