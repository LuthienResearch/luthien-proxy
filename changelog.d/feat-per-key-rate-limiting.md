---
category: Features
pr: 729
---

**Per-key rate limiting on /v1/ routes**: Token bucket rate limiter (in-process, asyncio-safe) applied to all `/v1/` requests. Configurable via `RATE_LIMIT_RPM` (requests per minute, 0=disabled) and `RATE_LIMIT_BURST` (burst size). Returns HTTP 429 with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers when exceeded.
