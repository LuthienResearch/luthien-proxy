"""Observability utilities for Luthien proxy v2.

This package provides:
- SimpleEventPublisher: Redis pub/sub bridge for real-time UI monitoring
- stream_activity_events: SSE streaming endpoint for activity monitor
- Integration between OpenTelemetry spans and legacy event system
"""

from .redis_event_publisher import RedisEventPublisher, stream_activity_events

__all__ = ["RedisEventPublisher", "stream_activity_events"]
