"""Webhook sender for conversation completion events.

Fires a POST request to a configurable endpoint when a conversation completes.
Exponential backoff with jitter, capped retry delay, bounded pending-task pool,
and a bounded drain window on shutdown.

**Delivery semantics: at-most-once, best-effort.** Failures after retries are
logged and dropped. Webhooks queued at shutdown beyond the drain window are
cancelled. Process crash mid-retry loses the event. Not suitable for systems
that require at-least-once delivery (use the durable Postgres event recorder
for that).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import TypedDict
from urllib.parse import ParseResult, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    """Last-resort safety net for fire-and-forget tasks.

    `_send_with_retries` already catches `Exception` per attempt; this only
    fires for true bugs that escape that try/except (e.g. a `BaseException`
    subclass other than `CancelledError`, or a defect in the retry loop
    itself). Don't use this as the primary failure-handling path.
    """
    if not task.cancelled() and (exc := task.exception()):
        logger.error("Webhook send task raised an unexpected exception: %r", exc)


# 4xx codes that ARE worth retrying — receiver-side transient signals.
# 408 Request Timeout, 425 Too Early, 429 Too Many Requests.
_RETRYABLE_4XX = frozenset({408, 425, 429})


SEND_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 1.0
DEFAULT_MAX_PENDING_TASKS = 1000
DEFAULT_SHUTDOWN_DRAIN_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 60.0


class _UsageCounts(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class ConversationCompletedPayload(TypedDict):
    """Payload sent to the webhook endpoint on conversation completion."""

    session_id: str | None
    transaction_id: str
    model: str
    usage: _UsageCounts
    duration_ms: int
    is_streaming: bool
    success: bool
    http_status: int
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
    success: bool,
    http_status: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> ConversationCompletedPayload:
    """Build the JSON payload for a conversation completion webhook.

    Args:
        session_id: Session identifier (from metadata.user_id or x-session-id header).
        transaction_id: Unique transaction/call ID for this request.
        model: Model name used for the conversation. Falls back to the literal
            string ``"unknown"`` if the upstream response had no model field
            and the request didn't either — consumers indexing on model should
            treat ``"unknown"`` as a sentinel rather than a real model.
        input_tokens: Number of input tokens consumed (excludes cache tokens).
        output_tokens: Number of output tokens generated.
        duration_ms: Total request duration in milliseconds. Note: streaming
            duration is measured at the generator's finally and so includes
            client-drain time — slow consumers inflate the number. Non-streaming
            duration is request-received → response-ready. The two are not the
            same measurement; consumers comparing latency across is_streaming
            should account for that.
        is_streaming: Whether the response was streamed.
        success: True iff the conversation completed cleanly and the client
            received the full response. False on errors and on streaming
            client-disconnect mid-flight.
        http_status: Final HTTP status the gateway returned (or would have
            returned, for streamed responses where headers were already sent).
        cache_creation_input_tokens: Anthropic prompt-cache write tokens.
        cache_read_input_tokens: Anthropic prompt-cache read tokens.

    Returns:
        Typed payload dict ready for JSON serialisation.
    """
    # total_tokens deliberately excludes cache tokens. Anthropic bills cache
    # writes at 1.25x and cache reads at 0.1x, so summing them naively would
    # mislead spend dashboards. Consumers computing total spend should weight
    # cache_creation_input_tokens and cache_read_input_tokens themselves.
    return ConversationCompletedPayload(
        session_id=session_id,
        transaction_id=transaction_id,
        model=model,
        usage=_UsageCounts(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
        duration_ms=duration_ms,
        is_streaming=is_streaming,
        success=success,
        http_status=http_status,
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
        max_pending_tasks: int = DEFAULT_MAX_PENDING_TASKS,
        shutdown_drain_seconds: float = DEFAULT_SHUTDOWN_DRAIN_SECONDS,
    ) -> None:
        """Initialize the webhook sender.

        Args:
            url: Webhook endpoint URL. If ``None`` or empty, the sender is disabled.
            max_retries: Number of retry attempts after the initial failure.
            retry_delay_seconds: Base delay between retries in seconds.
            max_pending_tasks: Maximum in-flight delivery tasks. New webhooks
                are dropped (with a warning log) when this cap is reached.
                Prevents unbounded memory growth when the endpoint is slow/down.
            shutdown_drain_seconds: On ``stop()``, wait up to this long for
                in-flight tasks to finish before cancelling survivors. Set to
                ``0`` for immediate cancel-only (legacy behavior).
        """
        if max_pending_tasks < 1:
            raise ValueError(
                f"max_pending_tasks must be >= 1 (got {max_pending_tasks}); "
                "to disable the webhook sender entirely, leave WEBHOOK_URL unset."
            )
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0 (got {max_retries})")
        if retry_delay_seconds < 0:
            raise ValueError(f"retry_delay_seconds must be >= 0 (got {retry_delay_seconds})")
        if shutdown_drain_seconds < 0:
            raise ValueError(
                f"shutdown_drain_seconds must be >= 0 (got {shutdown_drain_seconds}); "
                "use 0 for immediate-cancel-only on stop()."
            )
        self._url = url or None
        self._parsed_url: ParseResult | None = None
        if self._url:
            self._parsed_url = urlparse(self._url)
            scheme = self._parsed_url.scheme.lower()
            if scheme not in {"http", "https"}:
                logger.warning(
                    "WEBHOOK_URL scheme %r is not allowed (only 'http'/'https'). Webhook sender disabled.",
                    scheme,
                )
                self._url = None
                self._parsed_url = None
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._max_pending_tasks = max_pending_tasks
        self._shutdown_drain_seconds = shutdown_drain_seconds
        self._pending_tasks: set[asyncio.Task[None]] = set()
        self._dropped_due_to_backpressure = 0
        self._stopped = False
        self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) if self._url else None
        # safe_url is used in every retry/failure log; the URL is immutable
        # after construction so compute it once instead of urlparse-ing each call.
        self._safe_url = self._compute_safe_url()

    @property
    def enabled(self) -> bool:
        """True if a webhook URL is configured."""
        return bool(self._url)

    @property
    def pending_depth(self) -> int:
        """Current in-flight delivery task count. Useful for backpressure alerting."""
        return len(self._pending_tasks)

    @property
    def dropped_count(self) -> int:
        """Cumulative count of webhooks dropped due to pending-task cap (process lifetime)."""
        return self._dropped_due_to_backpressure

    @property
    def max_pending_tasks(self) -> int:
        """Configured cap on in-flight delivery tasks."""
        return self._max_pending_tasks

    @property
    def safe_url(self) -> str:
        """URL safe for logging — cached at construction time."""
        return self._safe_url

    def _compute_safe_url(self) -> str:
        """Compute the safe-for-logging URL: scheme + host:port, path/query/fragment redacted.

        Any path segment can be a secret (Slack/Discord/GitHub bake them in
        deeper paths; RequestBin/ngrok-style hooks bake them at the root).
        Preserving "the first path segment" was unsafe for the root case
        (https://host/SECRET → SECRET preserved). Redacting the whole path is
        the only safe default; operators identify the endpoint by host:port.
        """
        if not self._parsed_url:
            return ""
        parsed = self._parsed_url
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        safe_path = "/<redacted>" if parsed.path and parsed.path != "/" else parsed.path
        return urlunparse(parsed._replace(netloc=netloc, path=safe_path, query="", fragment=""))

    async def _attempt_send(self, payload: ConversationCompletedPayload) -> tuple[bool, bool]:
        """Attempt a single POST delivery.

        Args:
            payload: Conversation completion payload to send.

        Returns:
            (success, retryable). success=True iff 2xx. retryable=False signals
            the retry loop to bail (4xx client errors except 408/425/429 — these
            won't succeed on retry, so burning the budget just spams the log).
        """
        try:
            # self._client and self._url are paired: both None when disabled,
            # both set when enabled. Use if-guards (not assert) so behavior
            # matches under `python -O`, where asserts are stripped.
            if self._client is None or self._url is None:
                logger.error("Webhook client not initialized — skipping delivery")
                return False, False
            response = await self._client.post(self._url, json=dict(payload))
            status = response.status_code
            if status < 400:
                return True, False  # success; retryable irrelevant
            # 4xx: permanent unless explicitly transient (408/425/429).
            # 5xx: always retry.
            retryable = status >= 500 or status in _RETRYABLE_4XX
            logger.warning(
                "Webhook delivery failed: HTTP %d from %s%s",
                status,
                self.safe_url,
                "" if retryable else " (permanent — not retrying)",
            )
            return False, retryable
        except (httpx.HTTPError, TimeoutError, OSError):
            # Network errors are transient — retry.
            logger.warning("Webhook delivery error to %s", self.safe_url, exc_info=True)
            return False, True

    async def _send_with_retries(self, payload: ConversationCompletedPayload) -> None:
        """Deliver payload with exponential-backoff retries.

        Attempts delivery up to ``1 + max_retries`` times total. Failures after
        all retries are logged and silently discarded.

        Args:
            payload: Conversation completion payload to send.
        """
        # If the client/url invariant is broken, retries can't change that —
        # log once and bail rather than burning through N retries with the
        # same error log.
        if self._client is None or self._url is None:
            logger.error("Webhook client not initialized — skipping delivery (no retries)")
            return
        delay = self._retry_delay_seconds
        for attempt in range(1 + self._max_retries):
            try:
                success, retryable = await self._attempt_send(payload)
            except Exception:
                logger.error(
                    "Unexpected error in webhook delivery (attempt %d/%d) to %s",
                    attempt + 1,
                    1 + self._max_retries,
                    self.safe_url,
                    exc_info=True,
                )
                success = False
                # Unhandled exception is unlikely to be transient; treat as
                # permanent so misconfigured policies don't burn the retry budget.
                retryable = False

            if success:
                if attempt > 0:
                    logger.info("Webhook delivered successfully on attempt %d to %s", attempt + 1, self.safe_url)
                return

            if not retryable:
                logger.error(
                    "Webhook delivery to %s gave a permanent failure on attempt %d — not retrying",
                    self.safe_url,
                    attempt + 1,
                )
                return

            if attempt < self._max_retries:
                jittered = delay * (0.5 + random.random())  # ±50% jitter
                capped = min(jittered, MAX_RETRY_DELAY_SECONDS)
                logger.debug(
                    "Webhook delivery attempt %d/%d to %s failed, retrying in %.1fs",
                    attempt + 1,
                    1 + self._max_retries,
                    self.safe_url,
                    capped,
                )
                await asyncio.sleep(capped)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)

        logger.error(
            "Webhook delivery to %s failed after %d attempts — giving up",
            self.safe_url,
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
        success: bool,
        http_status: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        """Dispatch a webhook delivery as a background task.

        Returns immediately — never blocks the response path. If the sender is
        disabled, stopped, or at the pending-task cap, this is a no-op (with
        backpressure logging in the cap case).

        Args:
            session_id: Session identifier.
            transaction_id: Unique transaction ID.
            model: Model name actually used by the upstream (response-side
                preferred; request-side fallback).
            input_tokens: Input token count.
            output_tokens: Output token count.
            duration_ms: Request duration in milliseconds.
            is_streaming: Whether the response was streamed.
            success: True iff the conversation completed cleanly.
            http_status: Final HTTP status returned to the client.
            cache_creation_input_tokens: Anthropic prompt-cache write tokens.
            cache_read_input_tokens: Anthropic prompt-cache read tokens.
        """
        if not self.enabled or self._stopped:
            return

        if len(self._pending_tasks) >= self._max_pending_tasks:
            self._dropped_due_to_backpressure += 1
            n = self._dropped_due_to_backpressure
            # Decade thresholds for early signal, then every 1000 for sustained backpressure.
            if n in (1, 10, 100, 1000) or n % 1000 == 0:
                logger.warning(
                    "Webhook backpressure: dropped %d webhook(s) — pending task cap %d reached (url=%s)",
                    self._dropped_due_to_backpressure,
                    self._max_pending_tasks,
                    self.safe_url,
                )
            return

        payload = build_payload(
            session_id=session_id,
            transaction_id=transaction_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            is_streaming=is_streaming,
            success=success,
            http_status=http_status,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )
        # ensure_future + set.add are synchronous, so the task can't complete
        # before it's tracked. set.discard() (vs set.remove()) is also safe
        # if the discard callback runs after a manual prune.
        task = asyncio.ensure_future(self._send_with_retries(payload))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(_log_task_exception)

    async def stop(self) -> None:
        """Drain pending tasks (bounded), cancel survivors, close the HTTP client.

        Sequence:
          1. Mark stopped so subsequent fire_and_forget calls are no-ops.
          2. Wait up to ``shutdown_drain_seconds`` for in-flight deliveries
             (including their retry backoff sleeps) to finish.
          3. Cancel anything still running.
          4. Close the shared httpx client.

        Idempotent: subsequent calls return immediately. Safe to call from
        multiple shutdown paths (lifespan teardown + test fixture cleanup).
        """
        if self._stopped:
            return
        self._stopped = True
        if self._pending_tasks:
            tasks = list(self._pending_tasks)
            drained = 0
            cancelled = 0
            if self._shutdown_drain_seconds > 0:
                done, pending = await asyncio.wait(
                    tasks,
                    timeout=self._shutdown_drain_seconds,
                    return_when=asyncio.ALL_COMPLETED,
                )
                drained = len(done)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                cancelled = len(pending)
            else:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                cancelled = len(tasks)
            self._pending_tasks.clear()
            logger.info(
                "WebhookSender stopped: %d drained, %d cancelled, %d dropped (lifetime)",
                drained,
                cancelled,
                self._dropped_due_to_backpressure,
            )
        if self._client is not None:
            await self._client.aclose()
