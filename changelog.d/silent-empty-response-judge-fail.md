---
category: Fixes
pr: 451
---

**Empty stream returns error instead of silent HTTP 200**: When a policy emits zero streaming events (e.g., judge auth fails with `on_error: block`), the pipeline now yields an Anthropic-compatible error event so clients get a clear "policy evaluation unavailable" message instead of an empty successful response.
