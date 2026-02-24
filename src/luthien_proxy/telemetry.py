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

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from luthien_proxy.settings import get_settings
from luthien_proxy.utils.constants import OTEL_SPAN_ID_HEX_LENGTH, OTEL_TRACE_ID_HEX_LENGTH

logger = logging.getLogger(__name__)


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


def configure_tracing() -> trace.Tracer:
    """Configure OpenTelemetry tracing.

    Sets up:
    - Resource attributes (service name, version, environment)
    - OTLP exporter to Tempo
    - Batch span processor for efficiency

    Returns:
        Configured tracer for manual instrumentation
    """
    otel_enabled, otel_endpoint, service_name, service_version, environment = _get_otel_config()

    if not otel_enabled:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=false)")
        return trace.get_tracer(__name__)

    # Define resource attributes
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Configure OTLP exporter (sends to Tempo)
    otlp_exporter = OTLPSpanExporter(
        endpoint=otel_endpoint,
        insecure=True,  # No TLS for local dev
    )

    # Use batch processor for efficiency (batches spans before export)
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    logger.info(f"OpenTelemetry configured: {service_name} → {otel_endpoint}")

    return trace.get_tracer(__name__)


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

    FastAPIInstrumentor.instrument_app(app)
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
    "setup_telemetry",
    "tracer",
    "configure_tracing",
    "configure_logging",
    "instrument_app",
    "instrument_redis",
]
