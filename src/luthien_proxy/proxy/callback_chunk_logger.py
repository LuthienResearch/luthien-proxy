"""ABOUTME: Logging infrastructure for callback chunk processing (Step 6).

ABOUTME: Logs chunks received from control plane, normalization, and client yield.
"""

import logging
from typing import Any

from litellm._logging import verbose_logger as logger

from luthien_proxy.utils.constants import CONTENT_PREVIEW_MAX_LENGTH

logger.setLevel(logging.INFO)


class CallbackChunkLogger:
    """Logs callback chunk processing for debugging streaming pipeline."""

    def __init__(self, enabled: bool = True):
        """Initialize the callback chunk logger.

        Args:
            enabled: Whether logging is enabled (default: True)
        """
        self._enabled = enabled

    def log_control_chunk_received(
        self,
        stream_id: str,
        message: dict[str, Any],
        chunk_index: int,
    ) -> None:
        """Log a message received from control plane in poll_control()."""
        if not self._enabled:
            return

        msg_type = message.get("type", "UNKNOWN")
        logger.warning(
            "CALLBACK CONTROL IN  [%s] #%d: type=%s",
            stream_id,
            chunk_index,
            msg_type,
        )

    def log_chunk_normalized(
        self,
        stream_id: str,
        chunk: dict[str, Any],
        success: bool,
        error: str | None = None,
    ) -> None:
        """Log result of _normalize_stream_chunk()."""
        if not self._enabled:
            return

        if success:
            # Extract content preview
            choices = chunk.get("choices", [])
            content_preview = ""
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    content_preview = (
                        content[:CONTENT_PREVIEW_MAX_LENGTH]
                        + "..."
                        if len(content) > CONTENT_PREVIEW_MAX_LENGTH
                        else content
                    )

            logger.warning(
                "CALLBACK NORMALIZED  [%s]: success=True, content=%r",
                stream_id,
                content_preview,
            )
        else:
            logger.error(
                "CALLBACK NORMALIZED  [%s]: success=False, error=%s",
                stream_id,
                error,
            )

    def log_chunk_to_client(
        self,
        stream_id: str,
        chunk: dict[str, Any],
        chunk_index: int,
    ) -> None:
        """Log a chunk being yielded to the client."""
        if not self._enabled:
            return

        # Extract content preview
        choices = chunk.get("choices", [])
        content_preview = ""
        if choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                content_preview = (
                    content[:CONTENT_PREVIEW_MAX_LENGTH]
                    + "..."
                    if len(content) > CONTENT_PREVIEW_MAX_LENGTH
                    else content
                )

        logger.warning(
            "CALLBACK TO CLIENT   [%s] #%d: content=%r",
            stream_id,
            chunk_index,
            content_preview,
        )


# Singleton instance
_callback_chunk_logger: CallbackChunkLogger | None = None


def get_callback_chunk_logger() -> CallbackChunkLogger:
    """Get the global callback chunk logger instance."""
    global _callback_chunk_logger
    if _callback_chunk_logger is None:
        _callback_chunk_logger = CallbackChunkLogger(enabled=True)
    return _callback_chunk_logger
