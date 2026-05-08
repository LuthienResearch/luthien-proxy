---
category: Features
pr: 729
---

**Per-key rate limiting on /v1/ routes**: Token bucket rate limiter (in-process, asyncio-safe) applied to all `/v1/` requests. Configurable via `RATE_LIMIT_RPM` (requests per minute, 0=disabled) and `RATE_LIMIT_BURST` (absolute bucket capacity, defaults to RPM). Returns HTTP 429 with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers when exceeded.

**Operator notes:**
- In `CLIENT_KEY` auth mode, all users share one bucket (global limit, not per-user).
- In multi-replica or multi-worker deployments, effective per-key limit is `replicas × workers × RPM`. Size accordingly.
- `RATE_LIMIT_BURST` must be `>= RATE_LIMIT_RPM` (or 0 to default to RPM); setting burst below RPM silently caps sustainable throughput.
