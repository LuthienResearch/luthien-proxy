---
category: Fixes
---

**SimpleLLMPolicy: emit replacement blocks at monotonically increasing indices**: When a judge replaced one upstream content block with multiple blocks (N>1), all replacement blocks were emitted at the same index, producing an invalid Anthropic stream. Fixed by tracking `index_shift` on per-request state; replacement now emits at sequential indices and subsequent passthrough blocks are shifted to avoid collisions.
