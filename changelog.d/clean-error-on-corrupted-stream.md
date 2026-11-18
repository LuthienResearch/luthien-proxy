---
category: Fixes
pr: 800
---

**Corrupted outbound streams now fail with a clean client error instead of bricking the session**: the streaming protocol validator is enforced per-event in the pipeline (previously log-and-warn after the fact). When an outbound event violates Anthropic streaming event ordering (e.g. content blocks after `message_delta`, the PR #356 bug class), the proxy withholds the corrupting event, emits a structured SSE `error` event the client can parse, and ends the stream. The turn fails cleanly and the session stays usable; previously the corrupted stream was forwarded, the client reconstructed a malformed assistant message, and every subsequent request in the session returned 400.
  - End-of-stream rules (`message_stop` last, all blocks closed) remain advisory log-and-warn: they are only decidable after every event has already been forwarded.
  - Recorded as a `streaming.protocol_violation` policy event with `aborted: true`.
