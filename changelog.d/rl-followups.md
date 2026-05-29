---
category: Features
---

**Rate limiter follow-ups**: Added `RATE_LIMIT_MAX_KEYS` config field; the limiter now returns a transport-agnostic decision (HTTP translation moved to the route layer); successful `/v1/` responses carry `X-RateLimit-Remaining`; 429 rejections and bucket evictions emit structured warnings; and `dev-README.md` documents the per-process effective-RPM calculation (`RATE_LIMIT_RPM × uvicorn workers × replicas`).
