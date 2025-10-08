"""ABOUTME: Logging infrastructure for control plane streaming endpoint handling.

ABOUTME: Provides visibility into WebSocket messages received and policy invocations.
"""

import logging
from typing import Any

logger = logging.getLogger("luthien_proxy.control_plane.endpoint_logger")


class StreamingEndpointLogger:
    """Logs control plane endpoint handling for streaming requests."""

    def __init__(self, enabled: bool = True):
        """Initialize the endpoint logger.

        Args:
            enabled: Whether logging is enabled (default: True)
        """
        self._enabled = enabled

    def log_start_message(self, stream_id: str, request_data: dict[str, Any]) -> None:
        """Log the START message received from litellm."""
        if not self._enabled:
            return

        call_id = request_data.get("litellm_call_id", "unknown")
        model = request_data.get("model", "unknown")
        stream = request_data.get("stream", False)

        logger.info(
            "ENDPOINT START [%s]: call_id=%s, model=%s, stream=%s",
            stream_id,
            call_id,
            model,
            stream,
        )

    def log_incoming_chunk(self, stream_id: str, chunk: dict[str, Any], chunk_index: int) -> None:
        """Log a CHUNK message received from litellm (backend output)."""
        if not self._enabled:
            return

        # Extract content from chunk
        choices = chunk.get("choices", [])
        content_preview = ""
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                content_preview = content[:50] + "..." if len(content) > 50 else content

        logger.info(
            "ENDPOINT CHUNK IN [%s] #%d: content=%r",
            stream_id,
            chunk_index,
            content_preview,
        )

    def log_policy_invocation(self, stream_id: str, policy_class: str, request_data: dict[str, Any]) -> None:
        """Log when policy is invoked with stream context."""
        if not self._enabled:
            return

        call_id = request_data.get("litellm_call_id", "unknown")
        logger.info(
            "ENDPOINT POLICY [%s]: invoking %s for call_id=%s",
            stream_id,
            policy_class,
            call_id,
        )

    def log_outgoing_chunk(self, stream_id: str, chunk: dict[str, Any], chunk_index: int) -> None:
        """Log a CHUNK message being sent back to litellm (policy output)."""
        if not self._enabled:
            return

        # Extract content from chunk
        choices = chunk.get("choices", [])
        content_preview = ""
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                content_preview = content[:50] + "..." if len(content) > 50 else content

        logger.info(
            "ENDPOINT CHUNK OUT [%s] #%d: content=%r",
            stream_id,
            chunk_index,
            content_preview,
        )

    def log_end_message(self, stream_id: str) -> None:
        """Log when END message is sent back to litellm."""
        if not self._enabled:
            return

        logger.info("ENDPOINT END [%s]: stream complete", stream_id)

    def log_error(self, stream_id: str, error: str) -> None:
        """Log when an error occurs during streaming."""
        if not self._enabled:
            return

        logger.error("ENDPOINT ERROR [%s]: %s", stream_id, error)


# Singleton instance
_endpoint_logger: StreamingEndpointLogger | None = None


def get_endpoint_logger() -> StreamingEndpointLogger:
    """Get the global endpoint logger instance."""
    global _endpoint_logger
    if _endpoint_logger is None:
        _endpoint_logger = StreamingEndpointLogger(enabled=True)
    return _endpoint_logger
