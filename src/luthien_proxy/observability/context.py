# ABOUTME: ObservabilityContext provides unified interface for emitting events, metrics, and traces
# ABOUTME: Includes NoOp implementation for testing and Default implementation for production

"""Observability context for unified event/metric/trace emission."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict

from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
    from luthien_proxy.observability.sinks import LuthienRecordSink
    from luthien_proxy.utils.db import DatabasePool


# ===== Structured Records =====


class LuthienRecord(ABC):
    """Base class for structured observability records.

    Each subclass defines:
    - record_type: Class-level constant identifying the record type
    - __init__: Must call super().__init__(transaction_id) to set transaction_id

    Records are emitted via ObservabilityContext and flow to configured sinks:
    - Stdout (JSON logs for collection by log aggregators)
    - Database (persistent storage)
    - Redis (real-time event stream)
    - OTel spans (trace correlation)

    Serialization is automatic via __dict__, no need for to_dict() methods.
    """

    record_type: ClassVar[str]

    def __init__(self, transaction_id: str):
        """Initialize record with transaction context.

        Args:
            transaction_id: Unique identifier for the transaction this record belongs to
        """
        self.transaction_id = transaction_id


class PipelineRecord(LuthienRecord):
    """Record of a payload at a point in the pipeline.

    Tracks request/response data as it flows through transformations.

    Examples:
        - Client request (before processing)
        - Backend request (after policy modification)
        - Backend response (raw from upstream)
        - Client response (after formatting)
    """

    record_type = "pipeline"

    def __init__(self, transaction_id: str, pipeline_stage: str, payload: str):
        """Initialize pipeline record.

        Args:
            transaction_id: Unique identifier for this transaction
            pipeline_stage: Identifier for this stage (e.g., "client_request", "backend_response")
            payload: String representation of the data
        """
        super().__init__(transaction_id)
        self.pipeline_stage = pipeline_stage
        self.payload = payload


# ===== Observability Configuration =====

# Define valid sink names as a type-safe literal
SinkName = Literal["stdout", "db", "redis", "otel"]


class ObservabilityConfig(TypedDict, total=False):
    """Configuration for observability sink routing.

    Attributes:
        stdout_sink: Optional StdoutSink instance (default created if not provided)
        db_sink: Optional DatabaseSink instance (default created if not provided)
        redis_sink: Optional RedisSink instance (default created if not provided)
        otel_sink: Optional OTelSink instance (default created if not provided)
        routing: Maps LuthienRecord types to list of sink names
        default_sinks: Sink names to use for unspecified record types
    """

    stdout_sink: "LuthienRecordSink | None"
    db_sink: "LuthienRecordSink | None"
    redis_sink: "LuthienRecordSink | None"
    otel_sink: "LuthienRecordSink | None"
    routing: dict[type[LuthienRecord], list[SinkName]]
    default_sinks: list[SinkName]


# ===== Observability Context Interface =====


class ObservabilityContext(ABC):
    """Unified interface for observability operations."""

    @property
    @abstractmethod
    def span(self) -> Span:
        """Get the current OpenTelemetry span.

        Use this to directly add span attributes, events, etc. without wrappers.
        Example: obs_ctx.span.set_attribute("key", "value")
        """
        pass

    @abstractmethod
    def record(self, record: LuthienRecord) -> None:
        """Record a structured LuthienRecord (non-blocking).

        Routes the record to configured sinks based on routing configuration.

        Args:
            record: Structured record to emit
        """
        pass

    # TODO: Remove these compatibility methods once all code migrates to LuthienRecords
    def emit_event_nonblocking(self, event_type: str, data: dict, level: str = "INFO") -> None:  # noqa: ARG002
        """Deprecated: Use record() with LuthienRecord instead."""
        logger.warning(
            f"emit_event_nonblocking is deprecated, use record(LuthienRecord) instead (event_type={event_type})"
        )

    async def emit_event(self, event_type: str, data: dict, level: str = "INFO") -> None:  # noqa: ARG002
        """Deprecated: Use record() with LuthienRecord instead."""
        logger.warning(f"emit_event is deprecated, use record(LuthienRecord) instead (event_type={event_type})")


class NoOpObservabilityContext(ObservabilityContext):
    """No-op implementation for testing."""

    def __init__(self, *args, **kwargs):
        """Initialize NoOpObservabilityContext."""
        # Arguments accepted for signature compatibility but unused
        from opentelemetry.trace import INVALID_SPAN

        self._span = INVALID_SPAN

    @property
    def span(self) -> Span:
        """Return invalid span (no-op)."""
        return self._span

    def record(self, record: LuthienRecord) -> None:  # noqa: D102, ARG002
        """No-op record implementation."""
        pass


class DefaultObservabilityContext(ObservabilityContext):
    """Default implementation with configurable sink-based routing."""

    def __init__(
        self,
        transaction_id: str,
        span: Span,
        config: ObservabilityConfig | None = None,
        # Backward compatibility parameters (DEPRECATED)
        db_pool: "DatabasePool | None" = None,  # type: ignore
        event_publisher: "RedisEventPublisher | None" = None,  # type: ignore
    ):
        """Initialize DefaultObservabilityContext.

        Args:
            transaction_id: Unique identifier for this transaction
            span: OpenTelemetry span for distributed tracing
            config: Optional sink configuration (uses defaults if not provided)
            db_pool: DEPRECATED - pass DatabaseSink in config instead
            event_publisher: DEPRECATED - pass RedisSink in config instead
        """
        self._transaction_id = transaction_id
        self._span = span
        self._config = config or {}

        # Store deprecated parameters for backward compatibility
        self._db_pool = db_pool
        self._event_publisher = event_publisher

        # Build sink registry with defaults
        from luthien_proxy.observability.sinks import (
            DatabaseSink,
            OTelSink,
            RedisSink,
            StdoutSink,
        )

        self._sinks: dict[SinkName, "LuthienRecordSink"] = {
            "stdout": self._config.get("stdout_sink") or StdoutSink(),
            "db": self._config.get("db_sink") or DatabaseSink(None),  # type: ignore
            "redis": self._config.get("redis_sink") or RedisSink(None),  # type: ignore
            "otel": self._config.get("otel_sink") or OTelSink(span),
        }

        # Routing configuration
        self._routing: dict[type[LuthienRecord], list[SinkName]] = self._config.get("routing", {})
        self._default_sink_names: list[SinkName] = self._config.get("default_sinks", ["stdout"])

    @property
    def span(self) -> Span:
        """Get the current OpenTelemetry span."""
        return self._span

    def record(self, record: LuthienRecord) -> None:
        """Record a structured LuthienRecord (non-blocking).

        Routes the record to configured sinks based on record type.

        Args:
            record: Structured record to emit
        """
        # Determine which sinks should receive this record
        sink_names = self._routing.get(type(record), self._default_sink_names)

        async def _write_to_sinks() -> None:
            """Write record to all configured sinks."""
            for name in sink_names:
                try:
                    await self._sinks[name].write(record)
                except Exception as e:
                    logger.warning(f"Sink {name} failed to write record: {e}", exc_info=True)

        # Fire and forget - don't block on sink writes
        asyncio.create_task(_write_to_sinks())

    # Override deprecated methods to provide actual implementations for backward compatibility
    def emit_event_nonblocking(self, event_type: str, data: dict, level: str = "INFO") -> None:
        """Deprecated: Use record() with LuthienRecord instead."""
        logger.warning(
            f"emit_event_nonblocking is deprecated, use record(LuthienRecord) instead (event_type={event_type})"
        )
        # Delegate to async version but don't await (fire and forget)
        asyncio.create_task(self.emit_event(event_type, data, level))

    async def emit_event(self, event_type: str, data: dict, level: str = "INFO") -> None:
        """Deprecated: Use record() with LuthienRecord instead."""
        logger.warning(f"emit_event is deprecated, use record(LuthienRecord) instead (event_type={event_type})")

        # Enrich data with standard fields
        import time

        enriched_data = {
            "call_id": self._transaction_id,
            "timestamp": time.time(),
            **data,
        }

        # Add trace context if span is recording
        if self._span.is_recording():
            span_context = self._span.get_span_context()
            enriched_data["trace_id"] = format(span_context.trace_id, "032x")
            enriched_data["span_id"] = format(span_context.span_id, "016x")

        # Add to span as event
        self._span.add_event(event_type, enriched_data)

        # Emit to database if db_pool provided
        if self._db_pool:
            from luthien_proxy.storage.events import emit_custom_event

            await emit_custom_event(
                db_pool=self._db_pool,
                call_id=self._transaction_id,
                event_type=event_type,
                data=enriched_data,
            )

        # Publish to Redis if event_publisher provided
        if self._event_publisher:
            await self._event_publisher.publish_event(
                call_id=self._transaction_id,
                event_type=event_type,
                data=data,  # Redis gets unenriched data (matches old behavior)
            )


__all__ = [
    # Core types
    "ObservabilityContext",
    "NoOpObservabilityContext",
    "DefaultObservabilityContext",
    # Configuration
    "ObservabilityConfig",
    "SinkName",
    # Structured records
    "LuthienRecord",
    "PipelineRecord",
]
