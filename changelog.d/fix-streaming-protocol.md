---
category: Fixes
---

**Fix streaming protocol violations in SimpleLLMPolicy**: When a tool_use block
was blocked or replaced, the emitted events could violate the Anthropic streaming
protocol — duplicate `content_block_start` for text replacements, missing
`content_block_stop` for multi-block replacements, and incorrect indices. Added
`next_block_index` tracking to ensure each emitted block gets a proper
start/delta/stop triplet with sequential indices.
