---
category: Fixes
---

**Streaming no longer dies on code-execution responses**: `_format_sse_event` dumped SDK events in python mode, so a `message_start` carrying the code-execution container's `expires_at` datetime crashed `json.dumps` mid-stream and the client's stream ended in a generic `api_error`. Events are now dumped with `model_dump(mode="json")`, which serializes every wire field to JSON-safe values.
