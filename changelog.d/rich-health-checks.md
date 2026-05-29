---
category: Features
pr: 516
---

**Rich infrastructure diagnostics on `/api/admin/system-status`**: A new authenticated admin endpoint probes DB (`SELECT 1`) and Redis (`ping`) in parallel — each bounded by a 2s timeout — and reports per-component status with latency. Overall `status` reflects real infrastructure state: `healthy` / `degraded` (Redis unreachable) / `unhealthy` (DB unreachable); Redis absent (SQLite/local mode) is `not_configured` and does not affect the overall status.

`/health` stays a dependency-free liveness probe (`{status, version}`, always 200) so container/k8s liveness probes don't restart the gateway on a transient DB blip, and `/ready` continues to handle traffic-draining readiness. Keeping the rich checks behind admin auth avoids exposing latency/topology fingerprints — and an unbounded DB/Redis probe — on an unauthenticated endpoint.
