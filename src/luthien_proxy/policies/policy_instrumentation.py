"""ABOUTME: Policy instrumentation for debugging streaming response generation.

ABOUTME: Logs chunks received by policy and chunks yielded by policy.
"""

import logging
from typing import Any, AsyncIterator

from luthien_proxy.utils.constants import CONTENT_PREVIEW_MAX_LENGTH

logger = logging.getLogger("luthien_proxy.policies.policy_instrumentation")


class PolicyStreamLogger:
    """Logs policy stream processing for debugging."""

    def __init__(self, enabled: bool = True):
        """Initialize the policy stream logger.

        Args:
            enabled: Whether logging is enabled (default: True)
        """
        self._enabled = enabled

    def log_chunk_in(
        self,
        stream_id: str,
        policy_class: str,
        chunk: dict[str, Any],
        chunk_index: int,
    ) -> None:
        """Log a chunk received by the policy from backend."""
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
                    content[:CONTENT_PREVIEW_MAX_LENGTH] + "..."
                    if len(content) > CONTENT_PREVIEW_MAX_LENGTH
                    else content
                )

        logger.info(
            "POLICY CHUNK IN  [%s] %s #%d: content=%r",
            stream_id,
            policy_class,
            chunk_index,
            content_preview,
        )

    def log_chunk_out(
        self,
        stream_id: str,
        policy_class: str,
        chunk: dict[str, Any],
        chunk_index: int,
    ) -> None:
        """Log a chunk yielded by the policy to client."""
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
                    content[:CONTENT_PREVIEW_MAX_LENGTH] + "..."
                    if len(content) > CONTENT_PREVIEW_MAX_LENGTH
                    else content
                )

        logger.info(
            "POLICY CHUNK OUT [%s] %s #%d: content=%r",
            stream_id,
            policy_class,
            chunk_index,
            content_preview,
        )

    def log_stream_start(self, stream_id: str, policy_class: str) -> None:
        """Log when a policy stream begins processing."""
        if not self._enabled:
            return

        logger.info("POLICY STREAM START [%s] %s", stream_id, policy_class)

    def log_stream_end(self, stream_id: str, policy_class: str, total_chunks: int) -> None:
        """Log when a policy stream completes processing."""
        if not self._enabled:
            return

        logger.info(
            "POLICY STREAM END   [%s] %s: processed %d chunks",
            stream_id,
            policy_class,
            total_chunks,
        )


# Singleton instance
_policy_logger: PolicyStreamLogger | None = None


def get_policy_logger() -> PolicyStreamLogger:
    """Get the global policy stream logger instance."""
    global _policy_logger
    if _policy_logger is None:
        _policy_logger = PolicyStreamLogger(enabled=True)
    return _policy_logger


def instrument_policy_stream(
    stream_id: str,
    policy_class_name: str,
    incoming_stream: AsyncIterator[dict[str, Any]],
    outgoing_stream: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Wrap a policy's output stream with instrumentation logging.

    Args:
        stream_id: Stream identifier for correlation
        policy_class_name: Name of the policy class being instrumented
        incoming_stream: The async iterator of chunks received from backend
        outgoing_stream: The async iterator of chunks yielded by the policy

    Yields:
        Chunks from outgoing_stream, with logging of both in and out
    """
    return _instrumented_stream(stream_id, policy_class_name, outgoing_stream)


async def _instrumented_stream(
    stream_id: str,
    policy_class_name: str,
    stream: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Internal implementation of instrumented stream wrapper."""
    policy_logger = get_policy_logger()
    policy_logger.log_stream_start(stream_id, policy_class_name)

    chunk_out_index = 0
    try:
        async for chunk in stream:
            policy_logger.log_chunk_out(stream_id, policy_class_name, chunk, chunk_out_index)
            chunk_out_index += 1
            yield chunk
    finally:
        policy_logger.log_stream_end(stream_id, policy_class_name, chunk_out_index)
