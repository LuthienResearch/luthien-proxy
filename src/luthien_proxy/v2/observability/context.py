# ABOUTME: ObservabilityContext provides unified interface for emitting events, metrics, and traces
# ABOUTME: Includes NoOp implementation for testing and Default implementation for production

"""Observability context for unified event/metric/trace emission."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span

if TYPE_CHECKING:
    from luthien_proxy.utils.db import DatabasePool
    from luthien_proxy.v2.observability.redis_event_publisher import RedisEventPublisher


class ObservabilityContext(ABC):
    """Unified interface for observability operations."""

    @property
    @abstractmethod
    def transaction_id(self) -> str:
        """Transaction ID for this context."""

    @abstractmethod
    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event with automatic context enrichment."""

    @abstractmethod
    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record metric with automatic labels."""

    @abstractmethod
    def add_span_attribute(self, key: str, value: Any) -> None:
        """Add attribute to current span."""

    @abstractmethod
    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add event to current span."""


class NoOpObservabilityContext(ObservabilityContext):
    """No-op implementation for testing."""

    def __init__(self, transaction_id: str):  # noqa: D107
        self._transaction_id = transaction_id

    @property
    def transaction_id(self) -> str:  # noqa: D102
        return self._transaction_id

    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:  # noqa: D102
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

    @property
    def transaction_id(self) -> str:  # noqa: D102
        return self._transaction_id

    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit to DB, Redis, and OTel span."""
        import time

        enriched_data = {
            "call_id": self._transaction_id,
            "timestamp": time.time(),
            "trace_id": format(self.span.get_span_context().trace_id, "032x"),
            "span_id": format(self.span.get_span_context().span_id, "016x"),
            **data,
        }

        if self.db_pool:
            from luthien_proxy.v2.storage.events import emit_custom_event

            await emit_custom_event(
                call_id=self._transaction_id,
                event_type=event_type,
                data=enriched_data,
                db_pool=self.db_pool,
            )

        if self.event_publisher:
            await self.event_publisher.publish_event(call_id=self._transaction_id, event_type=event_type, data=data)

        self.add_span_event(event_type, data)

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
