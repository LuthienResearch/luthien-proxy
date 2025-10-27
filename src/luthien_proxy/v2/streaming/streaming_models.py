# ABOUTME: Data models for control plane interface using Pydantic
# ABOUTME: Protocol-agnostic models that work with both local and networked implementations

"""Data models for control plane interface."""

from __future__ import annotations


class StreamingError(Exception):
    """Error occurred during streaming response processing.

    The original exception(s) are available via __cause__.
    """

    pass


__all__ = [
    "StreamingError",
]
