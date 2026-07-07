---
category: Features
---

**Actionable error messages and retry-with-fix for fixable 400s**: Errors returned to clients now append a human-readable `Suggestion:` line to the raw upstream message (non-streaming responses and mid-stream SSE error events), and the pipeline automatically retries once, with the offending field stripped, when the upstream API rejects a request with an "Extra inputs are not permitted" 400. Repairs are observable via a `pipeline.retry_with_fix` event and a warning log; the raw upstream error text is always preserved.
