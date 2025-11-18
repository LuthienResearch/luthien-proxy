# ABOUTME: OpenTelemetry configuration for distributed tracing and logging
# ABOUTME: Exports traces to Tempo and correlates logs with trace context

"""OpenTelemetry setup for Luthien observability.

This module configures:
- Distributed tracing (exports to Tempo via OTLP)
- Structured logging with trace correlation
- Resource attributes (service name, version, etc.)
- Auto-instrumentation for FastAPI and Redis
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

# Configuration from environment
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() == "true"
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "http://tempo:4317")
SERVICE_NAME = os.getenv("SERVICE_NAME", "luthien-proxy")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "2.0.0")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def configure_tracing() -> trace.Tracer:
    """Configure OpenTelemetry tracing.

    Sets up:
    - Resource attributes (service name, version, environment)
    - OTLP exporter to Tempo
    - Batch span processor for efficiency

    Returns:
        Configured tracer for manual instrumentation
    """
    if not OTEL_ENABLED:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=false)")
        return trace.get_tracer(__name__)

    # Define resource attributes
    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": SERVICE_VERSION,
            "deployment.environment": ENVIRONMENT,
        }
    )

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Configure OTLP exporter (sends to Tempo)
    otlp_exporter = OTLPSpanExporter(
        endpoint=OTEL_ENDPOINT,
        insecure=True,  # No TLS for local dev
    )

    # Use batch processor for efficiency (batches spans before export)
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    logger.info(f"OpenTelemetry configured: {SERVICE_NAME} → {OTEL_ENDPOINT}")

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
    if not OTEL_ENABLED:
        return

    FastAPIInstrumentor.instrument_app(app)
    logger.info("FastAPI instrumented with OpenTelemetry")


def instrument_redis() -> None:
    """Instrument Redis client with OpenTelemetry.

    This automatically creates spans for Redis operations.
    """
    if not OTEL_ENABLED:
        return

    RedisInstrumentor().instrument()
    logger.info("Redis instrumented with OpenTelemetry")


def write_json_to_stdout(data: dict) -> None:
    """Write JSON log line to stdout with trace context.

    This is a low-level telemetry sink that writes directly to stdout,
    bypassing Python's logging framework. Promtail collects these logs.

    Use this for structured observability events. For regular log messages,
    use Python's logging (logger.info, logger.warning, etc.).

    Args:
        data: Dictionary of fields to include in the JSON log line
    """
    import json
    import sys
    from datetime import datetime

    # Get current span context
    span = trace.get_current_span()
    ctx = span.get_span_context()

    if ctx.is_valid:
        trace_id = format(ctx.trace_id, "032x")
        span_id = format(ctx.span_id, "016x")
    else:
        trace_id = "0" * 32
        span_id = "0" * 16

    # Build structured log entry
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "trace_id": trace_id,
        "span_id": span_id,
        **data,
    }

    # Write to stdout (promtail will collect this)
    print(json.dumps(log_entry), file=sys.stdout, flush=True)


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
            import json

            # Get current span context
            span = trace.get_current_span()
            ctx = span.get_span_context()

            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
            else:
                trace_id = "0" * 32
                span_id = "0" * 16

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
    "write_json_to_stdout",
]
