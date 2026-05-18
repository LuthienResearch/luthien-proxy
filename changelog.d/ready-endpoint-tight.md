---
category: Features
---

**Add /ready readiness probe**: New `GET /ready` endpoint returns 503 when the database is unreachable, times out, or dependencies are not initialized; 200 with `{"status": "ready"}` otherwise. Intended for ECS/k8s readiness probes so traffic is drained when the gateway cannot serve post-startup.
  - DB probe is bounded by `READY_DB_PROBE_TIMEOUT_SECONDS` (2s) via `asyncio.wait_for` so a slow database does not tarpit probe workers.
  - Error reasons are sanitized — no raw exception text or connection details are leaked to unauthenticated callers.
  - `/ready` is included in the no-cache middleware allowlist so CDNs cannot serve a stale cached response.
