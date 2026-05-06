"""OpenTelemetry setup for Luthien observability.

This module configures:
- Distributed tracing (exports via OTLP HTTP/protobuf by default; gRPC opt-in)
- Structured logging with trace correlation
- Resource attributes (service name, version, etc.)
- Auto-instrumentation for FastAPI and Redis

TLS behavior differs between exporters:
- gRPC uses ``insecure=True`` so plaintext local-dev endpoints work without TLS.
- HTTP/protobuf reads TLS from the URL scheme: ``http://`` is plaintext,
  ``https://`` verifies certificates. There is no ``insecure`` flag for HTTP.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from opentelemetry import trace
from opentelemetry.context import Context, attach, detach
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from luthien_proxy.settings import get_settings
from luthien_proxy.utils.constants import OTEL_SPAN_ID_HEX_LENGTH, OTEL_TRACE_ID_HEX_LENGTH

logger = logging.getLogger(__name__)

OTLP_PROTOCOL_HTTP: Final[str] = "http/protobuf"
OTLP_PROTOCOL_GRPC: Final[str] = "grpc"
SUPPORTED_OTLP_PROTOCOLS: Final[frozenset[str]] = frozenset({OTLP_PROTOCOL_HTTP, OTLP_PROTOCOL_GRPC})


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


def _get_otel_config() -> tuple[bool, str, str, str, str, str]:
    """Get OpenTelemetry configuration from settings.

    Returns:
        Tuple of (enabled, endpoint, service_name, service_version, environment, protocol)
    """
    settings = get_settings()
    return (
        settings.otel_enabled,
        settings.otel_exporter_otlp_endpoint,
        settings.service_name,
        settings.service_version,
        settings.environment,
        settings.otel_exporter_otlp_protocol,
    )


def _silence_otel_loggers() -> None:
    """Suppress noisy gRPC and OTel exporter logs.

    When the OTel collector/Tempo is unreachable, the gRPC transport and
    OTel SDK emit repeated ERROR-level messages. Push them to DEBUG so
    they only appear when someone explicitly enables debug logging.
    """
    for name in (
        "opentelemetry.exporter.otlp.proto.grpc.exporter",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.sdk.trace.export",
        "grpc._channel",
        "grpc._plugin_wrapping",
    ):
        logging.getLogger(name).setLevel(logging.DEBUG)


def _build_otlp_exporter(protocol: str, endpoint: str) -> SpanExporter:
    """Construct the OTLP span exporter for the configured protocol.

    Raises:
        ValueError: if ``protocol`` is not one of ``SUPPORTED_OTLP_PROTOCOLS``.
            We intentionally do not silently fall back to a default — a typo'd
            value should fail loud at startup, not silently drop traces.
    """
    if protocol == OTLP_PROTOCOL_GRPC:
        return GrpcSpanExporter(endpoint=endpoint, insecure=True)
    if protocol == OTLP_PROTOCOL_HTTP:
        # HttpSpanExporter uses the endpoint verbatim; the default endpoint
        # in config_fields.py already includes the /v1/traces path.
        return HttpSpanExporter(endpoint=endpoint)
    supported = ", ".join(sorted(SUPPORTED_OTLP_PROTOCOLS))
    raise ValueError(f"Unsupported OTEL_EXPORTER_OTLP_PROTOCOL={protocol!r}; expected one of: {supported}")


def configure_tracing() -> trace.Tracer:
    """Configure OpenTelemetry tracing.

    Sets up:
    - Resource attributes (service name, version, environment)
    - OTLP exporter (HTTP/protobuf by default; gRPC via OTEL_EXPORTER_OTLP_PROTOCOL=grpc)
    - Batch span processor for efficiency

    Returns:
        Configured tracer for manual instrumentation
    """
    otel_enabled, otel_endpoint, service_name, service_version, environment, protocol = _get_otel_config()

    if not otel_enabled:
        logger.debug("OpenTelemetry disabled (OTEL_ENABLED=false)")
        _silence_otel_loggers()
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

    # Configure OTLP exporter — HTTP/protobuf works behind HTTP load balancers
    # (ALB, nginx, Cloudflare) where gRPC fails with StatusCode.UNAVAILABLE.
    otlp_exporter = _build_otlp_exporter(protocol, otel_endpoint)

    # Use batch processor for efficiency (batches spans before export)
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    logger.info(f"OpenTelemetry configured: {service_name} → {otel_endpoint} ({protocol})")

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
    "restore_context",
    "setup_telemetry",
    "tracer",
    "configure_tracing",
    "configure_logging",
    "instrument_app",
    "instrument_redis",
]
