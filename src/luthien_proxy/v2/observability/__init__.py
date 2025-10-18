# ABOUTME: Observability utilities for Luthien proxy v2
# ABOUTME: Provides event publishing bridge to maintain real-time UI compatibility

"""Observability utilities for Luthien proxy v2.

This package provides:
- SimpleEventPublisher: Redis pub/sub bridge for real-time UI monitoring
- Integration between OpenTelemetry spans and legacy event system
"""

from .bridge import SimpleEventPublisher

__all__ = ["SimpleEventPublisher"]
