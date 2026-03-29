---
category: Bug Fixes
---

**Anthropic prompt cache tokens now forwarded**: `cache_creation_input_tokens` and `cache_read_input_tokens` are included in the response usage object when present, both for non-streaming and streaming responses. Previously these were silently dropped, preventing users from tracking prompt caching effectiveness.
