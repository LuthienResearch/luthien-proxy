"""Webhook sender for conversation completion events.

Fires a POST request to a configurable endpoint when a conversation completes.
Supports exponential-backoff retry logic for failed deliveries.
Failures are logged and discarded — never block the response path.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Log exceptions from fire-and-forget tasks to prevent silent failures."""
    if not task.cancelled() and (exc := task.exception()):
        logger.error("Webhook send task raised an unexpected exception: %r", exc)


SEND_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 1.0


class _UsageCounts(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ConversationCompletedPayload(TypedDict):
    """Payload sent to the webhook endpoint on conversation completion."""

    session_id: str | None
    transaction_id: str
    model: str
    usage: _UsageCounts
    duration_ms: int
    is_streaming: bool
    timestamp: str


def build_payload(
    *,
    session_id: str | None,
    transaction_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    is_streaming: bool,
) -> ConversationCompletedPayload:
    """Build the JSON payload for a conversation completion webhook.

    Args:
        session_id: Session identifier (from metadata.user_id or x-session-id header).
        transaction_id: Unique transaction/call ID for this request.
        model: Model name used for the conversation.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
        duration_ms: Total request duration in milliseconds.
        is_streaming: Whether the response was streamed.

    Returns:
        Typed payload dict ready for JSON serialisation.
    """
    return ConversationCompletedPayload(
        session_id=session_id,
        transaction_id=transaction_id,
        model=model,
        usage=_UsageCounts(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        duration_ms=duration_ms,
        is_streaming=is_streaming,
        timestamp=datetime.now(UTC).isoformat(),
    )


class WebhookSender:
    """Fire-and-forget webhook delivery with retry logic.

    Instances are singletons created at startup. The ``fire_and_forget`` method
    dispatches a background asyncio task so the response path is never blocked.

    Args:
        url: Webhook endpoint URL. If ``None`` or empty, the sender is disabled.
        max_retries: Number of retry attempts after the initial failure (default 3).
        retry_delay_seconds: Base delay between retries in seconds (default 1.0).
            Each retry doubles the delay (exponential backoff).
    """

    def __init__(
        self,
        *,
        url: str | None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        """Initialize the webhook sender.

        Args:
            url: Webhook endpoint URL. If ``None`` or empty, the sender is disabled.
            max_retries: Number of retry attempts after the initial failure.
            retry_delay_seconds: Base delay between retries in seconds.
        """
        self._url = url or None
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        """True if a webhook URL is configured."""
        return bool(self._url)

    async def _attempt_send(self, payload: ConversationCompletedPayload) -> bool:
        """Attempt a single POST delivery.

        Args:
            payload: Conversation completion payload to send.

        Returns:
            True on success (2xx response), False on any failure.
        """
        try:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
                # self._url is str when enabled (checked by caller); TypedDict value is str | None
                response = await client.post(self._url, json=dict(payload))  # type: ignore[arg-type]
                if response.status_code >= 400:
                    logger.warning(
                        "Webhook delivery failed: HTTP %d from %s",
                        response.status_code,
                        self._url,
                    )
                    return False
                return True
        except (httpx.HTTPError, asyncio.TimeoutError, OSError):
            logger.warning("Webhook delivery error to %s", self._url, exc_info=True)
            return False

    async def _send_with_retries(self, payload: ConversationCompletedPayload) -> None:
        """Deliver payload with exponential-backoff retries.

        Attempts delivery up to ``1 + max_retries`` times total. Failures after
        all retries are logged and silently discarded.

        Args:
            payload: Conversation completion payload to send.
        """
        delay = self._retry_delay_seconds
        for attempt in range(1 + self._max_retries):
            try:
                success = await self._attempt_send(payload)
            except Exception:
                logger.error(
                    "Unexpected error in webhook delivery (attempt %d/%d)",
                    attempt + 1,
                    1 + self._max_retries,
                    exc_info=True,
                )
                success = False

            if success:
                if attempt > 0:
                    logger.info("Webhook delivered successfully on attempt %d", attempt + 1)
                return

            if attempt < self._max_retries:
                logger.debug(
                    "Webhook delivery attempt %d/%d failed, retrying in %.1fs",
                    attempt + 1,
                    1 + self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff

        logger.error(
            "Webhook delivery to %s failed after %d attempts — giving up",
            self._url,
            1 + self._max_retries,
        )

    def fire_and_forget(
        self,
        *,
        session_id: str | None,
        transaction_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        is_streaming: bool,
    ) -> None:
        """Dispatch a webhook delivery as a background task.

        Returns immediately — never blocks the response path. If the sender is
        disabled (no URL configured), this is a no-op.

        Args:
            session_id: Session identifier.
            transaction_id: Unique transaction ID.
            model: Model name.
            input_tokens: Input token count.
            output_tokens: Output token count.
            duration_ms: Request duration in milliseconds.
            is_streaming: Whether the response was streamed.
        """
        if not self.enabled:
            return

        payload = build_payload(
            session_id=session_id,
            transaction_id=transaction_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            is_streaming=is_streaming,
        )
        task = asyncio.create_task(self._send_with_retries(payload))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(_log_task_exception)
