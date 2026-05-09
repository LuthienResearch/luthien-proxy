---
category: Features
pr: 741
---

**Webhook event export**: Fire-and-forget POST to `WEBHOOK_URL` on conversation completion (streaming + non-streaming) with session/transaction/model/usage/duration payload, plus `success`, `http_status`, and Anthropic prompt-cache token counts.
  - Configurable: `WEBHOOK_URL`, `WEBHOOK_MAX_RETRIES`, `WEBHOOK_RETRY_DELAY_SECONDS`, `WEBHOOK_MAX_PENDING_TASKS`, `WEBHOOK_SHUTDOWN_DRAIN_SECONDS`
  - Disabled when `WEBHOOK_URL` is empty
  - **Scope**: fires only on `/v1/messages` conversation completion; not wired into the `/v1/{path:path}` passthrough (count_tokens, models, etc. are not "completions")
  - Singleton `httpx.AsyncClient`, exponential backoff with jitter (capped at 60s), bounded pending-task pool, bounded drain on shutdown
  - URL sanitization in logs (full path redacted, userinfo stripped, IPv6 bracketed); rejects non-HTTP(S) schemes at construction
  - Backpressure observability: `pending_depth` / `dropped_count` / `max_pending_tasks` properties; admin endpoint `GET /api/admin/webhook/stats`
  - Streaming webhook is suppressed on bare client-disconnect (no false-success); fires with `success=False` on policy errors and empty streams
  - Non-streaming webhook fires with `success=False`, `http_status=<error code>` on policy/backend errors — symmetric with streaming
  - **`duration_ms` semantics**: non-streaming = request-received → response-ready; streaming = request-received → generator's finally (includes client-drain time, so slow consumers inflate the number). The two are not the same measurement.
  - **`success` semantics**: `success=True` means the gateway built and dispatched a response, not that the client received it. Both stream and non-stream fire from finally blocks before the response leaves the gateway. For at-least-once delivery confirmation, use the durable Postgres event recorder.
  - **At-most-once delivery**: failures after retries are dropped, shutdown drains then cancels, process crashes lose in-flight events. Not suitable for systems that require at-least-once or durable delivery.
