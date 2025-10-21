# ABOUTME: Observability utilities for Luthien proxy v2
# ABOUTME: Provides event publishing and streaming for real-time UI compatibility

"""Observability utilities for Luthien proxy v2.

This package provides:
- SimpleEventPublisher: Redis pub/sub bridge for real-time UI monitoring
- stream_activity_events: SSE streaming endpoint for activity monitor
- Integration between OpenTelemetry spans and legacy event system
"""

from .bridge import SimpleEventPublisher, stream_activity_events

__all__ = ["SimpleEventPublisher", "stream_activity_events"]
