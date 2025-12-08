"""Observability utilities for Luthien proxy.

This package provides:
- EventEmitter: Event emission to stdout, database, and redis (inject via Dependencies)
- EventEmitterProtocol: Protocol for event emitters (for type hints)
- NullEventEmitter: No-op emitter for tests
- RedisEventPublisher: Redis pub/sub bridge for real-time UI monitoring
- stream_activity_events: SSE streaming endpoint for activity monitor
"""

from .emitter import EventEmitter, EventEmitterProtocol, NullEventEmitter
from .redis_event_publisher import RedisEventPublisher, stream_activity_events

__all__ = [
    "EventEmitter",
    "EventEmitterProtocol",
    "NullEventEmitter",
    "RedisEventPublisher",
    "stream_activity_events",
]
