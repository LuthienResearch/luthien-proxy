"""Prometheus-compatible metric instruments for the Luthien gateway.

Instruments are created lazily via get_meter() so they pick up whatever
MeterProvider is configured at call time. Call configure_metrics() in
telemetry.py before the first request to wire them to the Prometheus exporter.

Exposed metrics:
  luthien_requests_total{streaming}          — cumulative request counter
  luthien_tokens_total{type}                 — cumulative token counter (input/output)
  luthien_request_duration_seconds{status}   — request latency histogram
  luthien_active_requests                    — in-flight request gauge
"""

from __future__ import annotations

from opentelemetry import metrics

from luthien_proxy.usage_telemetry.collector import UsageCollector

_METER_NAME = "luthien.proxy"

_meter = metrics.get_meter(_METER_NAME)

request_counter = _meter.create_counter(
    "luthien_requests_total",
    description="Total LLM proxy requests completed",
    unit="1",
)

token_counter = _meter.create_counter(
    "luthien_tokens_total",
    description="Total tokens consumed (input and output)",
    unit="1",
)

request_duration = _meter.create_histogram(
    "luthien_request_duration_seconds",
    description="LLM request latency in seconds",
    unit="s",
)

active_requests = _meter.create_up_down_counter(
    "luthien_active_requests",
    description="Number of LLM requests currently in flight",
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
        if input_tokens:
            token_counter.add(input_tokens, {"type": "input"})
        if output_tokens:
            token_counter.add(output_tokens, {"type": "output"})


__all__ = [
    "MetricsAwareUsageCollector",
    "active_requests",
    "request_counter",
    "request_duration",
    "token_counter",
]
