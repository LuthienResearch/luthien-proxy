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

    Logs *any* exception that escapes `_send_with_retries`. In normal flow
    that loop catches `Exception` around each attempt, so this only fires
    for: (a) bugs in the retry-loop scaffolding (e.g. a future refactor
    that moves work outside the catch); (b) non-`CancelledError`
    `BaseException` subclasses like `KeyboardInterrupt` or `SystemExit`.
    Don't use this as the primary failure-handling path.

    Safety: ``task.exception()`` raises ``InvalidStateError`` if the task
    isn't done. This function is registered via ``add_done_callback`` so it
    only runs after the task completes — calling it outside that context
    would break the walrus on the right of the ``and``.
    """
    if not task.cancelled() and (exc := task.exception()):
        logger.error("Webhook send task raised an unexpected exception: %r", exc)


# 4xx codes that ARE worth retrying — receiver-side transient signals.
# 408 Request Timeout, 425 Too Early, 429 Too Many Requests.
_RETRYABLE_4XX = frozenset({408, 425, 429})


# Bump when the payload schema changes in a way that requires receiver code
# changes (renames, removed fields, semantic changes). Additive fields don't
# require a bump — receivers should ignore unknown fields.
WEBHOOK_PAYLOAD_SCHEMA_VERSION = 1


DEFAULT_SEND_TIMEOUT_SECONDS = 10.0
# Sanity cap: combined with max_retries this bounds how long a single delivery
# can pin a slot. With send_timeout=300 + max_retries=20 that's already 100+
# minutes per slot — anything higher is misconfiguration.
SEND_TIMEOUT_CEILING_SECONDS = 300.0
# httpx default; cap webhook pool size at this even when max_pending_tasks is
# higher — most receivers can't sustain >100 concurrent TCP connections from a
# single client without overwhelming. Operators with the cap-as-concurrency
# expectation can lower max_pending_tasks instead.
DEFAULT_HTTPX_MAX_CONNECTIONS = 100
DEFAULT_MAX_RETRIES = 3
MAX_RETRIES_CEILING = 20
DEFAULT_RETRY_DELAY_SECONDS = 1.0
DEFAULT_MAX_PENDING_TASKS = 1000
MAX_PENDING_TASKS_CEILING = 100_000
DEFAULT_SHUTDOWN_DRAIN_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 60.0


class _UsageCounts(TypedDict):
    """Token usage from Anthropic's response.

    NOTE: ``total_tokens`` = ``input_tokens + output_tokens`` only — cache
    tokens are intentionally excluded because Anthropic bills cache writes
    at 1.25× and reads at 0.1×. Naive summation would mislead spend
    dashboards. Receivers building cost reports should weight
    ``cache_creation_input_tokens`` and ``cache_read_input_tokens``
    separately per their billing model.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class ConversationCompletedPayload(TypedDict):
    """Payload sent to the webhook endpoint on conversation completion.

    The `schema_version` field is the contract version. Bumps signal a
    breaking change (rename, removed field, semantic shift); additive fields
    don't bump. Receivers should ignore unknown fields and version-gate any
    field they treat as load-bearing.

    Notes for receivers:
      * `success=True` means the gateway built and dispatched a response, not
        that the client received it. Webhook fires from finally blocks before
        the response leaves the gateway. For at-least-once delivery
        confirmation use a durable record at the receiver side.
      * `total_tokens = input_tokens + output_tokens` only — cache tokens
        (`cache_creation_input_tokens` / `cache_read_input_tokens`) are
        surfaced separately so consumers can weight them per Anthropic's
        billing model (1.25x writes, 0.1x reads).
      * `model` may be the literal string `"unknown"` if neither response nor
        request carried a model field. Treat as a sentinel.
      * `transaction_id` is unique per request and stable across retries from
        the gateway side: the gateway never re-fires for the same transaction
        on its own. If the receiver returns 5xx and the gateway retries, the
        receiver will see two POSTs with the same `transaction_id` — use it
        as an idempotency key on the receiver side to dedupe.
    """

    schema_version: int
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
        success: True iff the gateway built and dispatched the full response
            (not necessarily that the client received it — the webhook fires
            from a finally block before bytes leave the gateway). False on
            errors, empty streams, and cancelled-before-emit. Streaming
            client-disconnect mid-flight suppresses the webhook entirely
            rather than firing with success=False.
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
        schema_version=WEBHOOK_PAYLOAD_SCHEMA_VERSION,
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

    Construction itself does not require a running event loop (modern
    ``httpx`` defers transport creation), but ``fire_and_forget`` schedules
    background tasks and so must be called under one. The gateway both
    constructs and uses the sender inside the FastAPI lifespan.

    Concurrency model: single-loop asyncio. Counter increments
    (``_dropped_due_to_backpressure``, ``_gave_up_after_retries``,
    ``_permanent_failures``) are safe under that model because no `await`
    appears between read and write. If anyone ever wraps `_attempt_send` in
    `loop.run_in_executor` (or otherwise hands work to another thread), the
    counters need an asyncio.Lock and the cap-check + create_task in
    `fire_and_forget` needs the same.

    See ``__init__`` for the full argument list.
    """

    def __init__(
        self,
        *,
        url: str | None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        max_pending_tasks: int = DEFAULT_MAX_PENDING_TASKS,
        shutdown_drain_seconds: float = DEFAULT_SHUTDOWN_DRAIN_SECONDS,
        send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS,
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
            send_timeout_seconds: Per-attempt HTTP timeout. Receivers doing
                synchronous downstream work (e.g. write-to-DB before ack) may
                need larger values than the default 10s.
        """
        if max_pending_tasks < 1:
            raise ValueError(
                f"max_pending_tasks must be >= 1 (got {max_pending_tasks}); "
                "to disable the webhook sender entirely, leave WEBHOOK_URL unset."
            )
        if max_pending_tasks > MAX_PENDING_TASKS_CEILING:
            raise ValueError(
                f"max_pending_tasks must be <= {MAX_PENDING_TASKS_CEILING} (got {max_pending_tasks}); "
                "the cap exists to bound memory under sustained backpressure — values this large defeat the point."
            )
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0 (got {max_retries})")
        if max_retries > MAX_RETRIES_CEILING:
            # Math: 1 + max_retries attempts × send_timeout + max_retries × backoff.
            # Use floats so sub-1s timeouts (e.g. 0.5) don't truncate to 0 in the error.
            attempts = 1 + max_retries
            sleeps = max_retries
            slot_seconds = attempts * send_timeout_seconds + sleeps * MAX_RETRY_DELAY_SECONDS
            raise ValueError(
                f"max_retries must be <= {MAX_RETRIES_CEILING} (got {max_retries}); "
                "high values combine multiplicatively with retry delays + send timeout. "
                f"At max_retries={max_retries}, a single failed delivery occupies one of "
                f"max_pending_tasks slots for up to ~{slot_seconds:.1f}s "
                f"({attempts} × {send_timeout_seconds}s timeout + {sleeps} × "
                f"{MAX_RETRY_DELAY_SECONDS:.0f}s backoff). "
                "A few of these against a sustained-failure receiver will fill the pool."
            )
        if retry_delay_seconds < 0:
            raise ValueError(f"retry_delay_seconds must be >= 0 (got {retry_delay_seconds})")
        # No upper bound here — actual sleeps are capped at MAX_RETRY_DELAY_SECONDS
        # in _send_with_retries, so a misconfigured retry_delay_seconds=10000
        # silently behaves like the cap. Symmetrical with send_timeout's floor/ceiling
        # would be clearer, but the runtime cap makes it operationally harmless.
        if shutdown_drain_seconds < 0:
            raise ValueError(
                f"shutdown_drain_seconds must be >= 0 (got {shutdown_drain_seconds}); "
                "use 0 for immediate-cancel-only on stop()."
            )
        # 0.1s floor: any lower and a routine TCP/TLS handshake against a
        # local receiver would time out, burning the full retry budget on
        # every event. The bound also keeps the slot-occupancy math from
        # __init__'s docstring honest.
        if send_timeout_seconds < 0.1:
            raise ValueError(
                f"send_timeout_seconds must be >= 0.1 (got {send_timeout_seconds}); "
                "lower values reliably time out routine TCP/TLS handshakes and burn the retry budget per event."
            )
        if send_timeout_seconds > SEND_TIMEOUT_CEILING_SECONDS:
            raise ValueError(
                f"send_timeout_seconds must be <= {SEND_TIMEOUT_CEILING_SECONDS:.0f} "
                f"(got {send_timeout_seconds}); combined with max_retries this defeats the "
                "point of max_pending_tasks (a single delivery would hold a slot for hours)."
            )
        # _url and _parsed_url are intentionally immutable after __init__.
        # _safe_url is cached from _parsed_url (line 343) and the port
        # validation below also runs once. If you ever support hot-reload
        # of WEBHOOK_URL (currently restart-required by config_fields design),
        # the new sender must invalidate _safe_url, re-run port validation,
        # and probably reconstruct the httpx.AsyncClient since its
        # User-Agent and pool size are also frozen at construction.
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
            elif not self._parsed_url.hostname:
                # `https://` parses cleanly but is unusable — every POST would
                # fail with a network error and burn the retry budget per request.
                # Log scheme only (mirrors the bad-scheme warning above) — the
                # full URL may carry secrets in path/userinfo.
                logger.warning(
                    "WEBHOOK_URL has no host (scheme %r). Webhook sender disabled.",
                    scheme,
                )
                self._url = None
                self._parsed_url = None
            else:
                # `urlparse(...).port` lazily parses the port and raises
                # ValueError if out of 0-65535 range (e.g. `:99999`). Surface
                # it as a disable-and-log here instead of crashing lifespan
                # later when _compute_safe_url() reads `.port`.
                try:
                    self._parsed_url.port  # noqa: B018 — eval to trigger validation
                except ValueError:
                    logger.warning(
                        "WEBHOOK_URL has invalid port (scheme %r). Webhook sender disabled.",
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
        # Cumulative count of webhooks the retry loop gave up on after exhausting
        # all attempts. Distinct from _dropped_due_to_backpressure (cap-reached
        # drop): retry-exhaustion means the receiver was reached but never
        # acknowledged. Both are "events the receiver never saw"; alerting
        # consumers should sum them for a true "lost events" count.
        self._gave_up_after_retries = 0
        # Cumulative count of webhooks rejected by permanent failure (4xx outside
        # the retryable set, or unexpected exception treated as permanent).
        # Distinct from _gave_up_after_retries (transient failure exhausted)
        # and _dropped_due_to_backpressure (cap-reached).
        self._permanent_failures = 0
        # Cumulative count of payload-construction failures swallowed in
        # _fire_webhook_for_completion (e.g. type drift in operator-policy
        # mutations of the response). Webhook never fires for these — the
        # receiver-visible loss looks identical to a never-attempted send.
        # Sets the failure surface to four counters total.
        self._payload_build_failures = 0
        self._stopped = False
        # UTC timestamp of construction — exposed for operators computing
        # drop-rates against dropped_count.
        self._started_at = datetime.now(UTC)
        # User-Agent: lets receivers identify Luthien webhooks vs other sources
        # (default httpx UA is `python-httpx/<version>` which is anonymous).
        # Connection pool: bound by min(max_pending_tasks, DEFAULT_HTTPX_MAX_CONNECTIONS).
        # Reasoning: the task cap bounds memory; the connection cap bounds
        # concurrency-against-receiver. They serve different purposes —
        # opening 1000 concurrent TCP connections to a single receiver
        # overwhelms most endpoints. Operators wanting the "cap-as-concurrency"
        # behavior should lower max_pending_tasks instead.
        pool_size = min(max_pending_tasks, DEFAULT_HTTPX_MAX_CONNECTIONS)
        self._client = (
            httpx.AsyncClient(
                timeout=send_timeout_seconds,
                # UA is intentionally schema-version, not gateway version: receivers
                # logging the UA shouldn't be able to fingerprint deploy SHA. The
                # payload's `schema_version` already serves the contract-version need.
                headers={"User-Agent": f"luthien-proxy-webhook/{WEBHOOK_PAYLOAD_SCHEMA_VERSION}"},
                limits=httpx.Limits(max_connections=pool_size),
            )
            if self._url
            else None
        )
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
        """Cumulative count of webhooks dropped due to pending-task cap (process lifetime).

        This counts only the cap-reached drop case. Webhooks that were
        accepted into the pool but exhausted their retries are counted in
        :pyattr:`gave_up_count`; webhooks rejected by permanent failure
        (4xx misconfig) are in :pyattr:`permanent_failure_count`. Alerting
        consumers should sum the three for the true "events the receiver
        never saw" rate.
        """
        return self._dropped_due_to_backpressure

    @property
    def gave_up_count(self) -> int:
        """Cumulative count of webhooks where _send_with_retries gave up after exhausting attempts."""
        return self._gave_up_after_retries

    @property
    def permanent_failure_count(self) -> int:
        """Cumulative count of webhooks rejected by permanent failure (non-retryable 4xx, etc.).

        This is the misconfig signal: 401/403/404/410/422 etc. mean the
        receiver URL is wrong or auth is rejected. Distinct from
        :pyattr:`dropped_count` (cap-reached) and :pyattr:`gave_up_count`
        (transient failure exhausted retries) and
        :pyattr:`payload_build_failure_count` (build-side bug). Sum the four
        for the true "events the receiver never saw" rate.
        """
        return self._permanent_failures

    @property
    def payload_build_failure_count(self) -> int:
        """Cumulative count of webhooks dropped before reaching the network.

        Increments when payload construction itself raises (type drift
        from operator-policy mutation, etc.). The receiver never sees
        anything; this surface is the only signal an operator gets.
        """
        return self._payload_build_failures

    def record_payload_build_failure(self) -> None:
        """Called by pipeline when the webhook fire wrapper catches a build-side error."""
        self._payload_build_failures += 1

    @property
    def max_pending_tasks(self) -> int:
        """Configured cap on in-flight delivery tasks."""
        return self._max_pending_tasks

    @property
    def started_at(self) -> datetime:
        """UTC timestamp the sender was constructed (process restart resets this)."""
        return self._started_at

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

        Note: ``parsed.port`` is read below and raises ValueError for ports
        outside 0-65535. The init guard above (search for "invalid port")
        validates this and disables the sender, so we only get here with a
        well-formed port. If init validation is ever relaxed, this method
        needs its own try/except.
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
            # TypedDict IS a dict at runtime; the cast was cosmetic.
            response = await self._client.post(self._url, json=payload)
            status = response.status_code
            if 200 <= status < 300:
                return True, False  # success; retryable irrelevant
            # 3xx: httpx doesn't follow redirects by default; if one reaches
            # us it's a misconfigured receiver. Treat as permanent (retrying
            # the same URL won't help — operator needs to update WEBHOOK_URL).
            # 4xx: permanent unless explicitly transient (408/425/429).
            # 5xx: always retry.
            # 5xx range only — hypothetical 6xx+ would be permanent.
            retryable = (500 <= status < 600) or status in _RETRYABLE_4XX
            # Per-attempt failures (retryable AND permanent) log at DEBUG;
            # the retry loop logs the user-visible outcome at WARN/ERROR.
            # Permanent failures bail after the first attempt, so the loop's
            # 'gave a permanent failure on attempt N — not retrying' carries
            # the same status in one line. Avoids 2× log per permanent fail
            # and 4× log per exhausted-retry request.
            logger.debug(
                "Webhook delivery failed: HTTP %d from %s%s",
                status,
                self.safe_url,
                "" if retryable else " (permanent — not retrying)",
            )
            return False, retryable
        except (httpx.HTTPError, TimeoutError, OSError):
            # Network errors are transient — retry. Per-attempt at DEBUG;
            # the retry loop logs the final outcome.
            logger.debug("Webhook delivery error to %s", self.safe_url, exc_info=True)
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
            had_unexpected_exception = False
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
                had_unexpected_exception = True

            if success:
                if attempt > 0:
                    logger.info("Webhook delivered successfully on attempt %d to %s", attempt + 1, self.safe_url)
                return

            if not retryable:
                self._permanent_failures += 1
                # Skip the second ERROR if we already logged the unexpected
                # exception above — prevents double-logging the same failure.
                if not had_unexpected_exception:
                    logger.error(
                        "Webhook delivery to %s gave a permanent failure on attempt %d — not retrying",
                        self.safe_url,
                        attempt + 1,
                    )
                return

            if attempt < self._max_retries:
                # Jitter as [0.5x, 1.0x] of base — keeps factor-of-2 spread but
                # the upper bound never exceeds `delay`. Previously `±50%`
                # ([0.5x, 1.5x]) collapsed against the MAX cap once delay
                # reached it: `min(60 * (0.5+random), 60)` left ~50% of the
                # distribution at exactly 60s, which is the opposite of what
                # jitter is for during downstream overload (thundering-herd).
                base = min(delay, MAX_RETRY_DELAY_SECONDS)
                capped = base * (0.5 + 0.5 * random.random())
                logger.debug(
                    "Webhook delivery attempt %d/%d to %s failed, retrying in %.1fs",
                    attempt + 1,
                    1 + self._max_retries,
                    self.safe_url,
                    capped,
                )
                await asyncio.sleep(capped)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)

        self._gave_up_after_retries += 1
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
            success: True iff the gateway built and dispatched the full
                response. See `ConversationCompletedPayload` for full
                semantics (success ≠ client received).
            http_status: Final HTTP status returned to the client.
            cache_creation_input_tokens: Anthropic prompt-cache write tokens.
            cache_read_input_tokens: Anthropic prompt-cache read tokens.
        """
        if not self.enabled or self._stopped:
            return

        # Invariant: this whole function is sync (no `await`), so the cap
        # check + create_task + set.add sequence is atomic under a single
        # event loop. If a future refactor introduces an `await` between
        # the len() check and the set.add, the cap can be exceeded and
        # the next sender will see >max_pending_tasks in pending_depth.
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
        # create_task + set.add are synchronous, so the task can't complete
        # before it's tracked. set.discard() (vs set.remove()) is also safe
        # if the discard callback runs after a manual prune.
        # Named so `asyncio.all_tasks()` during shutdown debugging shows
        # which transaction each in-flight task corresponds to.
        task = asyncio.create_task(
            self._send_with_retries(payload),
            name=f"webhook-{payload['transaction_id'][:8]}",
        )
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
                "WebhookSender stopped: %d completed (any terminal state), %d cancelled, %d dropped (lifetime)",
                drained,
                cancelled,
                self._dropped_due_to_backpressure,
            )
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                # aclose() raising during shutdown shouldn't taint subsequent
                # teardown steps in main.py's lifespan exit. Log and move on.
                logger.warning("WebhookSender: httpx client aclose() failed", exc_info=True)
