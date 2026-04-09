---
category: Features
pr: 516
---

**Rich health checks on `/health`**: The health endpoint now probes DB (`SELECT 1`) and Redis (`ping`) in parallel and reports per-component status with latency measurements. Overall `status` reflects real infrastructure state: `healthy` / `degraded` (Redis unreachable) / `unhealthy` (DB unreachable). Returns HTTP 200 always — callers inspect the body. Redis absent (SQLite/local mode) is `not_configured` and does not affect the overall status.
