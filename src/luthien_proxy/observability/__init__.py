"""Observability utilities for Luthien proxy.

This package provides:
- EventEmitter: Global event emission to stdout, database, and redis
- record_event/record_event_sync: Helper functions for recording events
- RedisEventPublisher: Redis pub/sub bridge for real-time UI monitoring
- stream_activity_events: SSE streaming endpoint for activity monitor
"""

from .emitter import configure_emitter, record_event, record_event_sync
from .redis_event_publisher import RedisEventPublisher, stream_activity_events

__all__ = [
    "configure_emitter",
    "record_event",
    "record_event_sync",
    "RedisEventPublisher",
    "stream_activity_events",
]
