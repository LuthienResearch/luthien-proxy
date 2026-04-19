---
category: Features
pr: 517
---

**Prometheus `/metrics` endpoint**: The gateway now exposes Prometheus-compatible metrics at `/metrics` via the OpenTelemetry Prometheus exporter.

Metrics exposed:
- `luthien_requests_completed_total{streaming}` — completed request counter
- `luthien_tokens_total{type}` — cumulative token counter (`input` / `output`)
- `luthien_request_duration_seconds{status,streaming}` — latency histogram for `/v1/messages` (full duration for non-streaming, TTFB for streaming — use `streaming` label to split)
- `luthien_active_requests` — in-flight request gauge

Implementation: `MetricsAwareUsageCollector` extends `UsageCollector` as a drop-in replacement — the existing external telemetry sender is unaffected. OTel `MeterProvider` with `PrometheusMetricReader` wired at startup alongside the existing `TracerProvider`.

Notes:
- `/metrics` is unauthenticated (standard Prometheus convention). Public deployments should restrict access at the network layer (ingress allowlist or private subnet).
- Multi-worker deployments (`uvicorn --workers N`) will report per-worker metrics; Prometheus scrapes will hit whichever worker the OS load-balances to. Current scripts run single-worker.
