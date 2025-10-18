# ABOUTME: V2 activity stream - provides real-time event streaming to Redis
# ABOUTME: Deprecated: Most events now handled via OpenTelemetry; only stream endpoint remains

"""V2 activity stream - streaming endpoint for real-time monitoring."""

from .stream import stream_activity_events

__all__ = [
    "stream_activity_events",
]
