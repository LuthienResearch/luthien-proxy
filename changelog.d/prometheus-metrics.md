---
category: Features
pr: 517
---

**Prometheus `/metrics` endpoint**: The gateway now exposes Prometheus-compatible metrics at `/metrics` via the OpenTelemetry Prometheus exporter.

Metrics exposed:
- `luthien_requests_total{streaming}` — cumulative request counter (completed requests)
- `luthien_tokens_total{type}` — cumulative token counter (`input` / `output`)
- `luthien_request_duration_seconds{status}` — request latency histogram for `/v1/messages`
- `luthien_active_requests` — in-flight request gauge

Implementation: `MetricsAwareUsageCollector` extends `UsageCollector` as a drop-in replacement — the existing external telemetry sender is unaffected. OTel `MeterProvider` with `PrometheusMetricReader` wired at startup alongside the existing `TracerProvider`.
