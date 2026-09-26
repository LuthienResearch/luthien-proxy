---
category: Features
pr: 796
---

**Multi-provider passthrough capture**: capture OpenAI (`/openai/*`) and Gemini (`/gemini/*`) passthrough traffic into `request_logs`, mirroring the existing Anthropic `/v1/*` capture.
  - Streaming and non-streaming responses are recorded; payloads are sanitized before persistence.
  - Cross-provider session grouping via the existing header/metadata contract, so a single logical conversation is retrievable regardless of provider.
  - Disabled by default: the routes forward client-supplied upstream credentials, so they only mount when `PASSTHROUGH_ROUTES_ENABLED=true` (otherwise they 404). This prevents an always-on deployment from acting as an open relay.
  - Streamed-response capture is bounded by `PASSTHROUGH_STREAM_CAPTURE_MAX_BYTES` (default 10 MiB); the client stream is never affected, and the recorded body is flagged `capture_truncated` beyond the limit.
