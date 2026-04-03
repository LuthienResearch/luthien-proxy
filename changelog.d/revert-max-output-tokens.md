---
category: Fixes
---

**Reject missing max_tokens**: Requests without `max_tokens` now return 400 instead of silently defaulting to 4096. Removed hallucinated `max_output_tokens` alias. Matches real Anthropic API behavior.
