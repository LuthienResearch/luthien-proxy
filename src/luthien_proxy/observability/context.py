# ABOUTME: ObservabilityContext provides unified interface for emitting events, metrics, and traces
# ABOUTME: Includes NoOp implementation for testing and Default implementation for production

"""Observability context for unified event/metric/trace emission."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
    from luthien_proxy.utils.db import DatabasePool


# ===== Structured Records =====


class LuthienRecord(ABC):
    """Base class for structured observability records.

    Each subclass defines:
    - record_type: Class-level constant identifying the record type
    - to_dict(): Serialization to JSON-compatible dict for logging

    Records are emitted via ObservabilityContext and flow to:
    - Loki (structured logs)
    - Database (persistent storage)
    - Redis (real-time event stream)
    - OTel spans (trace correlation)
    """

    record_type: ClassVar[str]

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize record to JSON-compatible dict.

        This is what gets logged to Loki/DB/Redis.
        Include all data needed for debugging and analysis.
        """
        ...


class LuthienPayloadRecord(LuthienRecord):
    """Record for tracking data at any point in the pipeline.

    Simple atomic record: just a stage name and arbitrary JSON data.
    Use this to log payloads, decisions, transformations, etc.

    Examples:
        - Request/response payloads at transformation points
        - Policy decisions and modifications
        - Format conversions
        - Performance metrics
    """

    record_type = "payload"

    def __init__(self, stage: str, data: dict[str, Any]):
        """Initialize payload record.

        Args:
            stage: Pipeline stage identifier (e.g., "client.request", "policy.modified", "backend.response")
            data: Arbitrary JSON-serializable data to log
        """
        self.stage = stage
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for logging."""
        return {
            "stage": self.stage,
            **self.data,
        }


def _check_level(level: str) -> str:
    valid_levels = logging._nameToLevel.keys()
    if level not in valid_levels:
        raise ValueError(f"Invalid level '{level}'. Must be one of {valid_levels}.")
    return level


class ObservabilityContext(ABC):
    """Unified interface for observability operations."""

    @abstractmethod
    async def emit_event(self, event_type: str, data: dict[str, Any], level: str = "INFO") -> None:
        """Emit event with automatic context enrichment."""

    def emit_event_nonblocking(self, event_type: str, data: dict[str, Any], level: str = "INFO") -> None:  # noqa: ARG002
        """Emit event without blocking (fire-and-forget).

        This schedules the emit_event coroutine as a background task. Use this when
        you don't want to block on event emission (e.g., in hot paths where observability
        shouldn't impact performance).

        Default implementation is a no-op. Override in subclasses that need actual emission.

        Args:
            event_type: Type of event to emit
            data: Event data dictionary
            level: Logging level emission (default: "INFO")
        """
        pass

    @abstractmethod
    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record metric with automatic labels."""

    @abstractmethod
    def add_span_attribute(self, key: str, value: Any) -> None:
        """Add attribute to current span."""

    @abstractmethod
    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add event to current span."""

    def record(self, record: LuthienRecord) -> None:
        """Record a structured LuthienRecord (non-blocking).

        Args:
            record: Structured record to emit
        """
        self.emit_event_nonblocking(
            event_type=f"luthien.{record.record_type}",
            data=record.to_dict(),
        )

    async def record_blocking(self, record: LuthienRecord) -> None:
        """Record a structured LuthienRecord (blocking variant).

        Args:
            record: Structured record to emit
        """
        await self.emit_event(
            event_type=f"luthien.{record.record_type}",
            data=record.to_dict(),
        )


class NoOpObservabilityContext(ObservabilityContext):
    """No-op implementation for testing."""

    def __init__(self, *args, **kwargs):
        """Initialize NoOpObservabilityContext."""
        # Span, db_pool, and event_publisher are accepted for signature compatibility but unused

    async def emit_event(self, event_type: str, data: dict[str, Any], level: str = "INFO") -> None:  # noqa: D102
        pass

    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:  # noqa: D102
        pass

    def add_span_attribute(self, key: str, value: Any) -> None:  # noqa: D102
        pass

    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:  # noqa: D102
        pass


class DefaultObservabilityContext(ObservabilityContext):
    """Default implementation using OTel + DB + Redis."""

    def __init__(  # noqa: D107
        self,
        transaction_id: str,
        span: Span,
        db_pool: "DatabasePool | None" = None,
        event_publisher: "RedisEventPublisher | None" = None,
    ):
        self._transaction_id = transaction_id
        self.span = span
        self.db_pool = db_pool
        self.event_publisher = event_publisher

    async def emit_event(self, event_type: str, data: dict[str, Any], level: str = "INFO") -> None:
        """Emit to DB, Redis, and OTel span."""
        _check_level(level)
        enriched_data = {
            "call_id": self._transaction_id,
            "timestamp": time.time(),
            "trace_id": format(self.span.get_span_context().trace_id, "032x"),
            "span_id": format(self.span.get_span_context().span_id, "016x"),
            **data,
        }

        if self.db_pool:
            from luthien_proxy.storage.events import emit_custom_event

            await emit_custom_event(
                call_id=self._transaction_id,
                event_type=event_type,
                data=enriched_data,
                db_pool=self.db_pool,
            )

        if self.event_publisher:
            await self.event_publisher.publish_event(call_id=self._transaction_id, event_type=event_type, data=data)

        self.add_span_event(event_type, data)

    def emit_event_nonblocking(self, event_type: str, data: dict[str, Any], level: str = "INFO") -> None:
        """Emit event without blocking (fire-and-forget).

        This schedules the emit_event coroutine as a background task. Use this when
        you don't want to block on event emission (e.g., in hot paths where observability
        shouldn't impact performance).
        """

        async def _emit_with_error_handling() -> None:
            try:
                await self.emit_event(event_type, data, level)
            except Exception as exc:
                logger.warning(
                    f"Nonblocking emit_event failed for event_type='{event_type}': {exc}",
                    exc_info=True,
                )

        asyncio.create_task(_emit_with_error_handling())

    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record metric with automatic labels."""
        from opentelemetry import metrics

        all_labels = {"call_id": self._transaction_id, **(labels or {})}
        meter = metrics.get_meter(__name__)
        counter = meter.create_counter(name)
        counter.add(value, all_labels)

    def add_span_attribute(self, key: str, value: Any) -> None:
        """Add attribute to current span."""
        self.span.set_attribute(key, value)

    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add event to current span."""
        self.span.add_event(name, attributes or {})


__all__ = [
    # Core types
    "ObservabilityContext",
    "NoOpObservabilityContext",
    "DefaultObservabilityContext",
    # Structured records
    "LuthienRecord",
    "LuthienPayloadRecord",
]
