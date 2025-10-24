# ABOUTME: Data models for control plane interface using Pydantic
# ABOUTME: Protocol-agnostic models that work with both local and networked implementations

"""Data models for control plane interface."""

from __future__ import annotations

from pydantic import BaseModel


class StreamingError(Exception):
    """Error occurred during streaming response processing.

    The original exception(s) are available via __cause__.
    """

    pass


class StreamingContext(BaseModel):
    """Context for streaming operations.

    This is created at the start of a stream and tracks state across chunks.
    """

    stream_id: str
    call_id: str
    chunk_count: int = 0


__all__ = [
    "StreamingError",
    "StreamingContext",
]
