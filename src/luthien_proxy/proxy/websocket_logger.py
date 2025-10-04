"""ABOUTME: WebSocket message logging for debugging streaming pipeline.

ABOUTME: Logs all messages sent/received over WebSocket connections between litellm and control plane.
"""

from typing import Any

from litellm._logging import verbose_proxy_logger as logger


class WebSocketMessageLogger:
    """Logs WebSocket messages for debugging streaming communication."""

    def __init__(self, enabled: bool = True):  # noqa: D107
        self._enabled = enabled

    def log_outgoing(self, stream_id: str, message: dict[str, Any]) -> None:
        """Log a message being sent from litellm to control plane."""
        if not self._enabled:
            return

        msg_type = message.get("type", "UNKNOWN")
        logger.info(
            "WebSocket OUT [%s]: type=%s, keys=%s",
            stream_id,
            msg_type,
            list(message.keys()),
        )

        # Log additional details based on message type
        if msg_type == "START":
            model = message.get("request_data", {}).get("model")
            logger.debug("  START: model=%s", model)
        elif msg_type == "CHUNK":
            data = message.get("data", {})
            chunk_id = data.get("id", "?")
            logger.debug("  CHUNK: id=%s", chunk_id)
        elif msg_type == "END":
            logger.debug("  END signal")

    def log_incoming(self, stream_id: str, message: dict[str, Any]) -> None:
        """Log a message received from control plane to litellm."""
        if not self._enabled:
            return

        msg_type = message.get("type", "UNKNOWN")
        logger.info(
            "WebSocket IN  [%s]: type=%s, keys=%s",
            stream_id,
            msg_type,
            list(message.keys()),
        )

        # Log additional details based on message type
        if msg_type == "CHUNK":
            data = message.get("data", {})
            if isinstance(data, dict):
                chunk_id = data.get("id", "?")
                choices = data.get("choices", [])
                logger.debug(
                    "  CHUNK: id=%s, choices=%d",
                    chunk_id,
                    len(choices) if isinstance(choices, list) else 0,
                )
        elif msg_type == "ERROR":
            error = message.get("error", "unknown")
            logger.debug("  ERROR: %s", error)
        elif msg_type == "END":
            logger.debug("  END signal")

    def log_json_error(self, stream_id: str, raw_data: str, error: Exception) -> None:
        """Log JSON parsing errors."""
        if not self._enabled:
            return

        logger.error(
            "WebSocket JSON error [%s]: %s, raw_data_preview=%s",
            stream_id,
            str(error),
            raw_data[:200] if len(raw_data) > 200 else raw_data,
        )


# Global singleton
_logger = WebSocketMessageLogger()


def get_websocket_logger() -> WebSocketMessageLogger:
    """Return the global WebSocket message logger."""
    return _logger


def enable_websocket_logging() -> None:
    """Enable WebSocket message logging."""
    _logger._enabled = True


def disable_websocket_logging() -> None:
    """Disable WebSocket message logging."""
    _logger._enabled = False
