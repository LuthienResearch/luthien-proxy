---
category: Features
---

**Webhook event export**: Fire-and-forget POST to `WEBHOOK_URL` on conversation completion (streaming + non-streaming) with session/transaction/model/usage/duration payload.
  - Configurable: `WEBHOOK_URL`, `WEBHOOK_MAX_RETRIES`, `WEBHOOK_RETRY_DELAY_SECONDS`, `WEBHOOK_MAX_PENDING_TASKS`
  - Disabled when `WEBHOOK_URL` is empty
  - Singleton `httpx.AsyncClient`, exponential backoff with jitter, bounded pending-task pool, graceful shutdown via `WebhookSender.stop()`
  - URL sanitization in logs (redacts userinfo + path-secret segments, brackets IPv6); rejects non-HTTP(S) schemes at construction
