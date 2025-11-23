# ABOUTME: ObservabilityContext provides unified interface for emitting events, metrics, and traces
# ABOUTME: Includes NoOp implementation for testing and Default implementation for production

"""Observability context for unified event/metric/trace emission."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict

from opentelemetry import trace
from opentelemetry.trace import INVALID_SPAN, Span

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
    from luthien_proxy.observability.sinks import LuthienRecordSink
    from luthien_proxy.utils.db import DatabasePool


# ===== Structured Records =====


# TODO: make these dataclasses or similar for better serialization; DON'T USE __dict__ FOR SERIALIZATION IN PRODUCTION
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


class GenericLuthienRecord(LuthienRecord):
    """Generic record for arbitrary observability events.

    Use this for custom events that don't fit predefined record types.
    """

    record_type = "generic"

    def __init__(self, transaction_id: str, event_type: str, data: dict):
        """Initialize generic record.

        Args:
            transaction_id: Unique identifier for this transaction
            event_type: Type of the event (e.g., "policy_decision", "llm_call")
            data: Arbitrary key-value data associated with the event
        """
        super().__init__(transaction_id)
        self.event_type = event_type
        self.data = data


# ===== Observability Configuration =====

# Define valid sink names as a type-safe literal
SinkName = Literal["stdout", "db", "redis"]


class ObservabilityConfig(TypedDict, total=False):
    """Configuration for observability sink routing.

    Attributes:
        stdout_sink: Optional StdoutSink instance (default created if not provided)
        db_sink: Optional DatabaseSink instance (default created if not provided)
        redis_sink: Optional RedisSink instance (default created if not provided)
        routing: Maps LuthienRecord types to list of sink names
        default_sinks: Sink names to use for unspecified record types
    """

    stdout_sink: "LuthienRecordSink | None"
    db_sink: "LuthienRecordSink | None"
    redis_sink: "LuthienRecordSink | None"
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

    def __init__(self, transaction_id: str, config: "ObservabilityConfig | None" = None):
        """Initialize NoOpObservabilityContext.

        Args:
            transaction_id: Unique identifier (unused in no-op implementation)
            config: Optional sink configuration (unused in no-op implementation)
        """
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
        config: ObservabilityConfig | None = None,
        # Backward compatibility parameters (DEPRECATED)
        db_pool: "DatabasePool | None" = None,
        event_publisher: "RedisEventPublisher | None" = None,
    ):
        """Initialize DefaultObservabilityContext.

        Args:
            transaction_id: Unique identifier for this transaction
            config: Optional sink configuration (uses defaults if not provided)
            db_pool: DEPRECATED - pass DatabaseSink in config instead
            event_publisher: DEPRECATED - pass RedisSink in config instead
        """
        self._transaction_id = transaction_id
        self._span = trace.get_current_span()  # Get auto-instrumented span
        self._config = config or {}

        # Store deprecated parameters for backward compatibility
        self._db_pool = db_pool
        self._event_publisher = event_publisher

        # Build sink registry with defaults
        # Import here to avoid circular imports with sinks module
        from luthien_proxy.observability.sinks import (  # noqa: PLC0415
            DatabaseSink,
            RedisSink,
            StdoutSink,
        )

        self._sinks: dict[SinkName, "LuthienRecordSink"] = {
            "stdout": self._config.get("stdout_sink") or StdoutSink(),
            "db": self._config.get("db_sink") or DatabaseSink(None),  # type: ignore
            "redis": self._config.get("redis_sink") or RedisSink(None),  # type: ignore
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

        Note:
            Span enrichment should be done at strategic points (policy decisions,
            LLM calls, errors) rather than on every record emission. See telemetry.py
            for span usage patterns.
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
            # Import here to avoid circular imports with storage module
            from luthien_proxy.storage.events import emit_custom_event  # noqa: PLC0415

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
