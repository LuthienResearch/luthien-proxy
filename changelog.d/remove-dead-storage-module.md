---
category: Chores & Docs
---

**Remove dead `storage` module**: Delete `src/luthien_proxy/storage/` and its tests. The module's only export, `reconstruct_full_response_from_chunks`, operated on OpenAI `chat.completions` chunk shape and had no non-test callers after the Anthropic-only gateway conversion (#351).
