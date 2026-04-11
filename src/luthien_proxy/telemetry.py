"""OpenTelemetry setup for Luthien observability.

This module configures:
- Distributed tracing (exports to Tempo via OTLP)
- Structured logging with trace correlation
- Resource attributes (service name, version, etc.)
- Auto-instrumentation for FastAPI and Redis
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.context import Context, attach, detach
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from luthien_proxy.settings import get_settings
from luthien_proxy.utils.constants import OTEL_SPAN_ID_HEX_LENGTH, OTEL_TRACE_ID_HEX_LENGTH

logger = logging.getLogger(__name__)


@contextmanager
def restore_context(ctx: Context) -> Generator[object, None, None]:
    """Attach an OpenTelemetry context and guarantee detach on exit.

    Use this in streaming generators where spans must be created as siblings
    under a parent context that was captured before the generator was yielded
    to the caller (e.g. FastAPI's StreamingResponse).

    Example::

        parent_ctx = context.get_current()


        async def stream():
            with restore_context(parent_ctx):
                with tracer.start_as_current_span("my_span"):
                    yield chunk

    Args:
        ctx: The OpenTelemetry context to attach.

    Yields:
        The token returned by ``context.attach()``.
    """
    token = attach(ctx)
    try:
        yield token
    finally:
        detach(token)


def _get_otel_config() -> tuple[bool, str, str, str, str]:
    """Get OpenTelemetry configuration from settings.

    Returns:
        Tuple of (enabled, endpoint, service_name, service_version, environment)
    """
    settings = get_settings()
    return (
        settings.otel_enabled,
        settings.otel_exporter_otlp_endpoint,
        settings.service_name,
        settings.service_version,
        settings.environment,
    )


def _build_resource() -> Resource:
    _, _, service_name, service_version, environment = _get_otel_config()
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )


def _silence_otel_loggers() -> None:
    """Suppress noisy gRPC and OTel exporter logs.

    When the OTel collector/Tempo is unreachable, the gRPC transport and
    OTel SDK emit repeated ERROR-level messages. Push them to DEBUG so
    they only appear when someone explicitly enables debug logging.
    """
    for name in (
        "opentelemetry.exporter.otlp.proto.grpc.exporter",
        "opentelemetry.sdk.trace.export",
        "grpc._channel",
        "grpc._plugin_wrapping",
    ):
        logging.getLogger(name).setLevel(logging.DEBUG)


def configure_tracing() -> trace.Tracer:
    """Configure OpenTelemetry tracing.

    Sets up:
    - Resource attributes (service name, version, environment)
    - OTLP exporter to Tempo
    - Batch span processor for efficiency

    Returns:
        Configured tracer for manual instrumentation
    """
    otel_enabled, otel_endpoint, service_name, _, _ = _get_otel_config()

    if not otel_enabled:
        logger.debug("OpenTelemetry disabled (OTEL_ENABLED=false)")
        _silence_otel_loggers()
        return trace.get_tracer(__name__)

    resource = _build_resource()
    provider = TracerProvider(resource=resource)

    otlp_exporter = OTLPSpanExporter(
        endpoint=otel_endpoint,
        insecure=True,
    )

    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    logger.info(f"OpenTelemetry configured: {service_name} → {otel_endpoint}")

    return trace.get_tracer(__name__)


_metrics_lock = threading.Lock()
_metrics_configured = False


def configure_metrics() -> None:
    """Configure OpenTelemetry metrics with a Prometheus exporter.

    Registers a PrometheusMetricReader into prometheus_client's global REGISTRY.
    Guarded to run exactly once — a second call is a no-op because
    PrometheusMetricReader raises ValueError on duplicate REGISTRY registration.

    Intentionally does not gate on otel_enabled: Prometheus scraping is useful
    even when OTLP trace export is disabled (e.g. local dev without Tempo).
    """
    global _metrics_configured  # noqa: PLW0603
    if _metrics_configured:
        return
    with _metrics_lock:
        if _metrics_configured:
            return

        resource = _build_resource()
        reader = PrometheusMetricReader()

        # OTel default histogram boundaries are [0, 5, 10, 25, ... 10000] — designed
        # for milliseconds. LLM TTFB ranges from ~0.1s to 120s, so we need custom
        # buckets to get useful p50/p95/p99 percentiles.
        ttfb_view = View(
            instrument_name="luthien_request_ttfb_seconds",
            aggregation=ExplicitBucketHistogramAggregation(
                boundaries=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0),
            ),
        )

        provider = MeterProvider(resource=resource, metric_readers=[reader], views=[ttfb_view])
        metrics.set_meter_provider(provider)
        _metrics_configured = True

    logger.info("Prometheus metrics endpoint enabled at /metrics")


def instrument_app(app) -> None:
    """Instrument FastAPI application with OpenTelemetry.

    This automatically creates spans for:
    - HTTP requests
    - Request/response timing
    - Status codes
    - Exceptions

    Args:
        app: FastAPI application instance
    """
    if not get_settings().otel_enabled:
        return

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics")
    logger.info("FastAPI instrumented with OpenTelemetry")


def instrument_redis() -> None:
    """Instrument Redis client with OpenTelemetry.

    This automatically creates spans for Redis operations.
    """
    if not get_settings().otel_enabled:
        return

    RedisInstrumentor().instrument()
    logger.info("Redis instrumented with OpenTelemetry")


def configure_logging() -> None:
    """Configure structured logging with trace correlation.

    This is for regular log messages (logger.info, logger.warning, etc.).
    For observability events, use emit_structured_log() instead.
    """

    # Simple JSON formatter for regular log messages
    class SimpleJSONFormatter(logging.Formatter):
        """Format regular log messages as JSON with trace context."""

        def format(self, record: logging.LogRecord) -> str:
            """Format log record as JSON."""
            # Get current span context
            span = trace.get_current_span()
            ctx = span.get_span_context()

            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
            else:
                trace_id = "0" * OTEL_TRACE_ID_HEX_LENGTH
                span_id = "0" * OTEL_SPAN_ID_HEX_LENGTH

            # Simple structure for regular logs
            log_data = {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "trace_id": trace_id,
                "span_id": span_id,
                "message": record.getMessage(),
            }

            return json.dumps(log_data)

    formatter = SimpleJSONFormatter()

    # Apply to root logger
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)


def setup_telemetry(app=None) -> trace.Tracer:
    """Setup all telemetry: tracing, logging, instrumentation.

    Call this once at application startup.

    Args:
        app: Optional FastAPI app to instrument

    Returns:
        Configured tracer for manual instrumentation
    """
    tracer = configure_tracing()
    configure_logging()
    instrument_redis()

    if app:
        instrument_app(app)

    return tracer


# Export commonly used tracer
tracer = trace.get_tracer(__name__)


__all__ = [
    "restore_context",
    "setup_telemetry",
    "tracer",
    "configure_tracing",
    "configure_logging",
    "configure_metrics",
    "instrument_app",
    "instrument_redis",
]
