# ABOUTME: Pure chunk-to-object aggregation for streaming responses
# ABOUTME: Generic utility for accumulating streamed data into complete objects

"""Stream aggregation utility.

This module provides StreamAggregator, a simple utility for turning
streaming chunks into aggregated objects. It handles the pure data
transformation without any lifecycle management, callbacks, or policy logic.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from luthien_proxy.utils.streaming_aggregation import StreamChunkAggregator, ToolCallState

T = TypeVar("T")


class StreamAggregator(Generic[T]):
    """Aggregates streaming chunks into a complete object.

    This is a thin wrapper around the underlying aggregation logic that
    provides a clean interface for chunk accumulation.

    Currently specialized for tool call aggregation, but designed to be
    extended for other stream types (content, thinking blocks, etc.)

    Example:
        aggregator = StreamAggregator()
        for chunk in chunks:
            aggregator.add_chunk(chunk)

        if aggregator.is_complete():
            result = aggregator.get_result()
    """

    def __init__(self) -> None:
        """Initialize aggregator with empty state."""
        self._aggregator = StreamChunkAggregator()

    def add_chunk(self, chunk_dict: dict) -> None:
        """Add a chunk to the aggregation.

        Args:
            chunk_dict: Dictionary representation of the chunk
        """
        self._aggregator.capture_chunk(chunk_dict)

    def is_complete(self) -> bool:
        """Check if aggregation has received finish_reason.

        Returns:
            True if stream has indicated completion
        """
        return self._aggregator.finish_reason is not None

    def get_finish_reason(self) -> str | None:
        """Get the finish reason if available.

        Returns:
            Finish reason string, or None if not yet received
        """
        return self._aggregator.finish_reason

    def get_tool_call_state(self) -> ToolCallState | None:
        """Get the current tool call state.

        Returns:
            ToolCallState if a tool call exists, None otherwise
        """
        if not self._aggregator.tool_calls:
            return None

        # Return first tool call state (typically one per index)
        return list(self._aggregator.tool_calls.values())[0]

    def get_content(self) -> str:
        """Get accumulated content text.

        Returns:
            Joined content string
        """
        return self._aggregator.get_accumulated_content()

    def get_role(self) -> str | None:
        """Get the role if set.

        Returns:
            Role string, or None if not yet received
        """
        return self._aggregator.role


__all__ = ["StreamAggregator"]
