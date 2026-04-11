"""Prometheus-compatible metric instruments for the Luthien gateway.

Instruments are created eagerly at import time against the global MeterProvider.
OTel's _ProxyMeter delegates to the real provider once configure_metrics()
(telemetry.py) sets it — so import order doesn't matter as long as
configure_metrics() runs before the first request.

NOTE: In multi-worker deployments (uvicorn --workers N), each worker maintains
independent in-memory metrics. Prometheus will scrape whichever worker the OS
load-balances to, producing incomplete numbers. Current scripts run single-worker.

Exposed metrics:
  luthien_requests_completed_total{streaming} — completed request counter
  luthien_tokens_total{type}                  — cumulative token counter (input/output)
  luthien_request_ttfb_seconds{status}        — time-to-first-byte histogram
  luthien_active_requests                     — in-flight request gauge (TTFB-scoped)
"""

from __future__ import annotations

from opentelemetry import metrics

from luthien_proxy.usage_telemetry.collector import UsageCollector

_METER_NAME = "luthien.proxy"

_meter = metrics.get_meter(_METER_NAME)

request_counter = _meter.create_counter(
    "luthien_requests_completed_total",
    description="Total LLM proxy requests that completed successfully",
    unit="1",
)

token_counter = _meter.create_counter(
    "luthien_tokens_total",
    description="Total tokens consumed (input and output)",
    unit="1",
)

request_duration = _meter.create_histogram(
    "luthien_request_ttfb_seconds",
    description="Time-to-first-byte for LLM requests in seconds (BaseHTTPMiddleware measures TTFB, not full streaming duration)",
    unit="s",
)

active_requests = _meter.create_up_down_counter(
    "luthien_active_requests",
    description="LLM requests in flight (decrements at TTFB due to BaseHTTPMiddleware; streaming requests appear shorter than actual)",
    unit="1",
)


class MetricsAwareUsageCollector(UsageCollector):
    """UsageCollector that also increments Prometheus metric instruments.

    Drop-in replacement for UsageCollector. The external telemetry sender
    (snapshot_and_reset) is unaffected — it still gets the same counters.
    """

    def record_completed(self, *, is_streaming: bool) -> None:
        """Record completed request and increment Prometheus request counter."""
        super().record_completed(is_streaming=is_streaming)
        request_counter.add(1, {"streaming": str(is_streaming).lower()})

    def record_tokens(self, *, input_tokens: int, output_tokens: int) -> None:
        """Record token usage and increment Prometheus token counter."""
        super().record_tokens(input_tokens=input_tokens, output_tokens=output_tokens)
        token_counter.add(input_tokens, {"type": "input"})
        token_counter.add(output_tokens, {"type": "output"})


__all__ = [
    "MetricsAwareUsageCollector",
    "active_requests",
    "request_counter",
    "request_duration",
    "token_counter",
]
