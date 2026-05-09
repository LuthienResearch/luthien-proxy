"""Tests for webhook sender module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from luthien_proxy.webhook.sender import (
    ConversationCompletedPayload,
    WebhookSender,
    build_payload,
)


def _fire_kwargs(**overrides: object) -> dict[str, object]:
    """Return default fire_and_forget kwargs, with overrides merged in."""
    defaults: dict[str, object] = {
        "session_id": "s",
        "transaction_id": "t",
        "model": "m",
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_ms": 0,
        "is_streaming": False,
        "success": True,
        "http_status": 200,
    }
    defaults.update(overrides)
    return defaults


def _payload(**overrides: object) -> ConversationCompletedPayload:
    """Build a payload dict with sensible defaults for testing."""
    base: dict[str, object] = {
        "session_id": None,
        "transaction_id": "t",
        "model": "m",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "duration_ms": 0,
        "is_streaming": False,
        "success": True,
        "http_status": 200,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


# ── Payload builder tests ──────────────────────────────────────────────────────


def test_build_payload_non_streaming():
    """build_payload returns correct structure for non-streaming success."""
    payload = build_payload(
        session_id="sess-123",
        transaction_id="txn-abc",
        model="claude-3-5-sonnet-20241022",
        input_tokens=100,
        output_tokens=50,
        duration_ms=1234,
        is_streaming=False,
        success=True,
        http_status=200,
    )
    assert payload["session_id"] == "sess-123"
    assert payload["transaction_id"] == "txn-abc"
    assert payload["model"] == "claude-3-5-sonnet-20241022"
    assert payload["usage"]["input_tokens"] == 100
    assert payload["usage"]["output_tokens"] == 50
    assert payload["usage"]["total_tokens"] == 150
    assert payload["usage"]["cache_creation_input_tokens"] == 0
    assert payload["usage"]["cache_read_input_tokens"] == 0
    assert payload["duration_ms"] == 1234
    assert payload["is_streaming"] is False
    assert payload["success"] is True
    assert payload["http_status"] == 200
    assert "timestamp" in payload


def test_build_payload_streaming():
    """build_payload marks streaming correctly."""
    payload = build_payload(
        session_id=None,
        transaction_id="txn-xyz",
        model="claude-opus-4-5",
        input_tokens=200,
        output_tokens=300,
        duration_ms=5000,
        is_streaming=True,
        success=True,
        http_status=200,
    )
    assert payload["session_id"] is None
    assert payload["is_streaming"] is True
    assert payload["usage"]["total_tokens"] == 500


def test_build_payload_includes_cache_tokens():
    """Cache token counts are surfaced in the usage dict for prompt-cache analytics."""
    payload = build_payload(
        session_id="s",
        transaction_id="t",
        model="m",
        input_tokens=10,
        output_tokens=5,
        duration_ms=1,
        is_streaming=False,
        success=True,
        http_status=200,
        cache_creation_input_tokens=42,
        cache_read_input_tokens=99,
    )
    assert payload["usage"]["cache_creation_input_tokens"] == 42
    assert payload["usage"]["cache_read_input_tokens"] == 99


def test_build_payload_failure():
    """success=False / non-200 status is captured in the payload."""
    payload = build_payload(
        session_id="s",
        transaction_id="t",
        model="m",
        input_tokens=0,
        output_tokens=0,
        duration_ms=0,
        is_streaming=True,
        success=False,
        http_status=503,
    )
    assert payload["success"] is False
    assert payload["http_status"] == 503


# ── WebhookSender fixtures ────────────────────────────────────────────────────


@pytest.fixture
async def sender():
    s = WebhookSender(url="https://example.com/webhook")
    yield s
    await s.stop()


@pytest.fixture
async def sender_with_retries():
    s = WebhookSender(url="https://example.com/webhook", max_retries=3, retry_delay_seconds=0.01)
    yield s
    await s.stop()


@pytest.fixture
async def make_sender():
    """Factory that constructs WebhookSender instances and stops them all in teardown.

    Use for tests that need bespoke construction args — avoids the httpx.AsyncClient
    leak from direct ``WebhookSender(url=...)`` calls without a paired ``stop()``.
    """
    instances: list[WebhookSender] = []

    def _make(**kwargs) -> WebhookSender:
        s = WebhookSender(**kwargs)
        instances.append(s)
        return s

    yield _make
    for s in instances:
        await s.stop()


# ── _attempt_send tests ───────────────────────────────────────────────────────


async def _drain_pending(sender: WebhookSender) -> None:
    """Wait deterministically for all in-flight delivery tasks to complete."""
    while sender._pending_tasks:
        await asyncio.gather(*list(sender._pending_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_send_success(sender):
    """Successful delivery returns (True, _) and makes one POST."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    sender._client = mock_client

    success, _retryable = await sender._attempt_send(_payload())
    assert success is True
    mock_client.post.assert_called_once()


@pytest.mark.parametrize(
    "status, retryable_expected",
    [
        (500, True),  # 5xx: transient
        (502, True),
        (503, True),
        (408, True),  # 4xx exceptions: transient
        (425, True),
        (429, True),
        (400, False),  # 4xx default: permanent
        (401, False),
        (403, False),
        (404, False),
        (410, False),
        (415, False),
        (422, False),
    ],
)
@pytest.mark.asyncio
async def test_send_classifies_retryability_per_status(sender, status: int, retryable_expected: bool):
    """4xx is permanent except 408/425/429; 5xx is always retryable."""
    mock_response = MagicMock()
    mock_response.status_code = status
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    sender._client = mock_client

    success, retryable = await sender._attempt_send(_payload())
    assert success is False
    assert retryable is retryable_expected


@pytest.mark.asyncio
async def test_send_network_error_is_retryable(sender):
    """Network errors return (False, True) — transient, will be retried."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    sender._client = mock_client

    success, retryable = await sender._attempt_send(_payload())
    assert success is False
    assert retryable is True


# ── fire_and_forget tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_and_forget_success(sender):
    """fire_and_forget dispatches a background task that succeeds."""
    with patch.object(sender, "_attempt_send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = (True, False)
        sender.fire_and_forget(**_fire_kwargs())
        await _drain_pending(sender)
        mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_fire_and_forget_retries_on_transient_failure(sender_with_retries):
    """fire_and_forget retries up to max_retries on transient (retryable) failures."""
    call_count = 0

    async def fail_twice_then_succeed(payload):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return True, False
        return False, True  # retryable failure

    with patch.object(sender_with_retries, "_attempt_send", side_effect=fail_twice_then_succeed):
        sender_with_retries.fire_and_forget(**_fire_kwargs())
        await _drain_pending(sender_with_retries)
        assert call_count == 3


@pytest.mark.asyncio
async def test_fire_and_forget_gives_up_after_max_retries(sender_with_retries):
    """fire_and_forget stops after max_retries exhausted on transient failures."""
    call_count = 0

    async def always_transient_fail(payload):
        nonlocal call_count
        call_count += 1
        return False, True  # retryable but never succeeds

    with patch.object(sender_with_retries, "_attempt_send", side_effect=always_transient_fail):
        sender_with_retries.fire_and_forget(**_fire_kwargs())
        await _drain_pending(sender_with_retries)
        assert call_count == 4  # 1 initial + 3 retries


@pytest.mark.asyncio
async def test_fire_and_forget_bails_on_permanent_failure(sender_with_retries):
    """Permanent (non-retryable) failures bail immediately — no retries."""
    call_count = 0

    async def always_permanent_fail(payload):
        nonlocal call_count
        call_count += 1
        return False, False  # permanent failure

    with patch.object(sender_with_retries, "_attempt_send", side_effect=always_permanent_fail):
        sender_with_retries.fire_and_forget(**_fire_kwargs())
        await _drain_pending(sender_with_retries)
        assert call_count == 1  # no retries


@pytest.mark.asyncio
async def test_fire_and_forget_no_crash_on_exception():
    """fire_and_forget does not propagate exceptions to caller; task completes within retries."""
    s = WebhookSender(url="https://example.com/webhook", max_retries=2, retry_delay_seconds=0.001)

    async def raise_exception(payload):
        raise RuntimeError("unexpected error")

    with patch.object(s, "_attempt_send", side_effect=raise_exception):
        s.fire_and_forget(**_fire_kwargs())
        await _drain_pending(s)
    await s.stop()


# ── Disabled / scheme rejection ──────────────────────────────────────────────


def test_sender_disabled_when_no_url():
    assert WebhookSender(url=None).enabled is False


@pytest.mark.parametrize(
    "kwargs, msg_fragment",
    [
        ({"max_pending_tasks": 0}, "max_pending_tasks must be >= 1"),
        ({"max_pending_tasks": -1}, "max_pending_tasks must be >= 1"),
        ({"max_retries": -1}, "max_retries must be >= 0"),
        ({"retry_delay_seconds": -0.1}, "retry_delay_seconds must be >= 0"),
    ],
)
def test_construction_rejects_invalid_args(kwargs, msg_fragment):
    """Invalid construction args raise ValueError so misconfig fails loud, not silent.

    Earlier behavior: max_pending_tasks=0 silently dropped every webhook
    because the cap-check is `>= max_pending_tasks`. Now caught at construction.
    """
    with pytest.raises(ValueError, match=msg_fragment):
        WebhookSender(url=None, **kwargs)


@pytest.mark.asyncio
async def test_sender_enabled_when_url_set(make_sender):
    assert make_sender(url="https://example.com/hook").enabled is True


@pytest.mark.parametrize("bad_scheme", ["file:///etc/passwd", "javascript://x", "ftp://example.com/hook"])
def test_bad_scheme_disables_sender(bad_scheme: str):
    """Non-HTTP(S) schemes log a warning and disable the sender rather than raising.

    Bad-scheme path skips httpx.AsyncClient construction entirely, so no leak.
    """
    sender = WebhookSender(url=bad_scheme)
    assert not sender.enabled
    assert sender.safe_url == ""


@pytest.mark.asyncio
async def test_fire_and_forget_noop_when_disabled():
    sender = WebhookSender(url=None)
    with patch.object(sender, "_attempt_send", new_callable=AsyncMock) as mock_send:
        sender.fire_and_forget(**_fire_kwargs())
        await asyncio.sleep(0.05)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_fire_and_forget_noop_after_stop():
    """Once stop() has been called, fire_and_forget does not schedule new tasks."""
    sender = WebhookSender(url="https://example.com/hook")
    await sender.stop()
    with patch.object(sender, "_attempt_send", new_callable=AsyncMock) as mock_send:
        sender.fire_and_forget(**_fire_kwargs())
        await asyncio.sleep(0.05)
        mock_send.assert_not_called()


# ── safe_url tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_url_strips_userinfo_redacts_path(make_sender):
    """Credentials are stripped, port is preserved, path is fully redacted."""
    sender = make_sender(url="https://user:pass@hooks.example.com:8443/webhook?key=secret")
    assert sender.safe_url == "https://hooks.example.com:8443/<redacted>"


@pytest.mark.asyncio
async def test_safe_url_no_path(make_sender):
    """URL without a path stays clean."""
    sender = make_sender(url="https://hooks.example.com")
    assert sender.safe_url == "https://hooks.example.com"


@pytest.mark.asyncio
async def test_safe_url_empty_path_root(make_sender):
    """Bare '/' path is preserved (no secret to leak)."""
    sender = make_sender(url="https://hooks.example.com/")
    assert sender.safe_url == "https://hooks.example.com/"


@pytest.mark.asyncio
async def test_safe_url_strips_query_and_fragment(make_sender):
    sender = make_sender(url="https://hooks.example.com/webhook?token=abc#section")
    safe = sender.safe_url
    assert "token" not in safe
    assert "abc" not in safe
    assert "section" not in safe


@pytest.mark.asyncio
async def test_safe_url_redacts_multi_segment_path(make_sender):
    """Slack/Discord-style multi-segment secrets are redacted."""
    sender = make_sender(url="https://hooks.slack.com/services/T123/B456/SECRET_TOKEN")
    safe = sender.safe_url
    assert "SECRET_TOKEN" not in safe
    assert "T123" not in safe
    assert "B456" not in safe


@pytest.mark.asyncio
async def test_safe_url_redacts_single_segment_secret(make_sender):
    """Single-segment path secrets (RequestBin/ngrok/custom hooks) are redacted.

    Regression: previous logic preserved the first path
    segment, so https://x/SECRET leaked SECRET intact.
    """
    sender = make_sender(url="https://hooks.example.com/SECRET_AT_ROOT")
    safe = sender.safe_url
    assert "SECRET_AT_ROOT" not in safe
    assert safe == "https://hooks.example.com/<redacted>"


@pytest.mark.asyncio
async def test_safe_url_brackets_ipv6(make_sender):
    """IPv6 hosts get bracketed; port preserved."""
    sender = make_sender(url="http://[::1]:8080/hook")
    safe = sender.safe_url
    assert "[::1]" in safe
    assert ":8080" in safe


def test_safe_url_empty_when_no_url():
    assert WebhookSender(url=None).safe_url == ""


# ── Backpressure / observability ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_and_forget_drops_when_backpressure_cap_reached(make_sender):
    """fire_and_forget drops new webhooks once _pending_tasks reaches max_pending_tasks."""
    sender = make_sender(url="https://example.com/hook", max_pending_tasks=2)

    send_started = asyncio.Event()
    block_release = asyncio.Event()

    async def _blocking_send(payload):
        send_started.set()
        await block_release.wait()
        return True

    with patch.object(sender, "_attempt_send", side_effect=_blocking_send):
        for i in range(2):
            sender.fire_and_forget(**_fire_kwargs(session_id=f"s{i}", transaction_id=f"t{i}"))
        await send_started.wait()
        assert sender.pending_depth == 2
        assert sender.dropped_count == 0

        sender.fire_and_forget(**_fire_kwargs(session_id="dropped", transaction_id="dropped"))
        assert sender.pending_depth == 2
        assert sender.dropped_count == 1

        block_release.set()
        await asyncio.gather(*list(sender._pending_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_observability_properties(make_sender):
    """Counters are exposed via public properties."""
    sender = make_sender(url="https://example.com/hook", max_pending_tasks=42)
    assert sender.pending_depth == 0
    assert sender.dropped_count == 0
    assert sender.max_pending_tasks == 42


@pytest.mark.asyncio
async def test_fire_and_forget_smoke_many_tasks():
    """Smoke test: many rapid fire_and_forget calls don't crash (e.g. KeyError from discard).

    This isn't a true race test — `ensure_future(...)` and `_pending_tasks.add(task)`
    are both synchronous, so the loop never gets a chance to run between them. But
    if a future refactor introduces a yield point between them, this would catch
    the resulting KeyError on discard.
    """
    sender = WebhookSender(url="https://example.com/hook")

    async def fast_send(payload):
        return True

    with patch.object(sender, "_attempt_send", side_effect=fast_send):
        for _ in range(20):
            sender.fire_and_forget(**_fire_kwargs())
        await asyncio.sleep(0.1)
    await sender.stop()


# ── Backpressure log cadence ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backpressure_log_decade_thresholds(make_sender, caplog):
    """Backpressure log fires at 1, 10, 100, 1000, then every 1000.

    Uses max_pending_tasks=1 with a primed pending task that never completes,
    so every additional fire_and_forget hits the cap. (Validation rejects
    max_pending_tasks=0, so that approach is unavailable.)
    """
    import logging

    sender = make_sender(url="https://example.com/hook", max_pending_tasks=1)
    block_release = asyncio.Event()

    async def _blocking_send(payload):
        await block_release.wait()
        return True

    with patch.object(sender, "_attempt_send", side_effect=_blocking_send):
        # Prime the pool with one task that won't complete during the test.
        sender.fire_and_forget(**_fire_kwargs())
        # Let the primed task start so pending_depth == 1.
        await asyncio.sleep(0.01)
        with caplog.at_level(logging.WARNING, logger="luthien_proxy.webhook.sender"):
            for _ in range(150):
                sender.fire_and_forget(**_fire_kwargs())

        log_lines = [r.message for r in caplog.records if "backpressure" in r.message.lower()]
        # Decade thresholds: n=1, 10, 100 → 3 messages within the first 150 drops.
        assert len(log_lines) == 3, f"expected 3 log lines, got {len(log_lines)}: {log_lines}"
        assert sender.dropped_count == 150
        block_release.set()


# ── stop() — drain semantics ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_drains_completing_tasks_within_timeout():
    """stop() waits for in-flight tasks that finish within the drain window."""
    sender = WebhookSender(url="https://example.com/hook", shutdown_drain_seconds=2.0)

    completed = asyncio.Event()

    async def quick_send(payload):
        await asyncio.sleep(0.05)
        completed.set()
        return True

    with patch.object(sender, "_attempt_send", side_effect=quick_send):
        sender.fire_and_forget(**_fire_kwargs())
        await sender.stop()
    assert completed.is_set(), "stop() should have allowed the in-flight task to complete"
    assert sender.pending_depth == 0


@pytest.mark.asyncio
async def test_stop_cancels_tasks_exceeding_drain_timeout():
    """stop() cancels in-flight tasks that don't finish within the drain window."""
    sender = WebhookSender(url="https://example.com/hook", shutdown_drain_seconds=0.05)

    block_release = asyncio.Event()

    async def blocking_send(payload):
        await block_release.wait()
        return True

    with patch.object(sender, "_attempt_send", side_effect=blocking_send):
        sender.fire_and_forget(**_fire_kwargs())
        await asyncio.sleep(0.01)
        assert sender.pending_depth == 1
        await sender.stop()
    assert sender.pending_depth == 0
    # Sanity: the block_release was never set — task must have been cancelled.
    assert not block_release.is_set()


@pytest.mark.asyncio
async def test_stop_immediate_cancel_when_drain_zero():
    """shutdown_drain_seconds=0 reverts to immediate cancellation (legacy behavior)."""
    sender = WebhookSender(url="https://example.com/hook", shutdown_drain_seconds=0.0)
    block_release = asyncio.Event()

    async def blocking_send(payload):
        await block_release.wait()
        return True

    with patch.object(sender, "_attempt_send", side_effect=blocking_send):
        sender.fire_and_forget(**_fire_kwargs())
        await asyncio.sleep(0.01)
        assert sender.pending_depth == 1
        await sender.stop()
    assert sender.pending_depth == 0


@pytest.mark.asyncio
async def test_stop_is_noop_when_no_pending_tasks():
    sender = WebhookSender(url="https://example.com/hook")
    await sender.stop()
    assert sender.pending_depth == 0


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    """A second stop() call returns immediately rather than re-aclose-ing the client."""
    with patch("luthien_proxy.webhook.sender.httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.aclose = AsyncMock()
        mock_client_cls.return_value = mock_instance

        sender = WebhookSender(url="https://example.com/hook")
        await sender.stop()
        await sender.stop()
        await sender.stop()

        mock_instance.aclose.assert_called_once()


# ── Singleton client tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_client_created_across_retries():
    """AsyncClient is instantiated once at construction, not per-attempt."""
    with patch("luthien_proxy.webhook.sender.httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=MagicMock(status_code=200))
        mock_instance.aclose = AsyncMock()
        mock_client_cls.return_value = mock_instance

        sender = WebhookSender(
            url="https://example.com/hook",
            max_retries=2,
            retry_delay_seconds=0.001,
        )
        for i in range(5):
            sender.fire_and_forget(**_fire_kwargs(session_id=f"s{i}", transaction_id=f"t{i}"))
        await asyncio.sleep(0.1)
        mock_client_cls.assert_called_once()


@pytest.mark.asyncio
async def test_stop_closes_client():
    with patch("luthien_proxy.webhook.sender.httpx.AsyncClient") as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.aclose = AsyncMock()
        mock_client_cls.return_value = mock_instance

        sender = WebhookSender(url="https://example.com/hook")
        await sender.stop()
        mock_instance.aclose.assert_called_once()
