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

# ── Payload builder tests ──────────────────────────────────────────────────────


def test_build_payload_non_streaming():
    """build_payload returns correct structure for non-streaming response."""
    payload = build_payload(
        session_id="sess-123",
        transaction_id="txn-abc",
        model="claude-3-5-sonnet-20241022",
        input_tokens=100,
        output_tokens=50,
        duration_ms=1234,
        is_streaming=False,
    )
    assert payload["session_id"] == "sess-123"
    assert payload["transaction_id"] == "txn-abc"
    assert payload["model"] == "claude-3-5-sonnet-20241022"
    assert payload["usage"]["input_tokens"] == 100
    assert payload["usage"]["output_tokens"] == 50
    assert payload["usage"]["total_tokens"] == 150
    assert payload["duration_ms"] == 1234
    assert payload["is_streaming"] is False
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
    )
    assert payload["session_id"] is None
    assert payload["is_streaming"] is True
    assert payload["usage"]["total_tokens"] == 500


def test_build_payload_zero_tokens():
    """build_payload handles zero token counts."""
    payload = build_payload(
        session_id="s",
        transaction_id="t",
        model="m",
        input_tokens=0,
        output_tokens=0,
        duration_ms=0,
        is_streaming=False,
    )
    assert payload["usage"]["total_tokens"] == 0


# ── WebhookSender tests ────────────────────────────────────────────────────────


@pytest.fixture
def sender():
    return WebhookSender(url="https://example.com/webhook")


@pytest.fixture
def sender_with_retries():
    return WebhookSender(url="https://example.com/webhook", max_retries=3, retry_delay_seconds=0.01)


@pytest.mark.asyncio
async def test_send_success(sender):
    """Successful delivery returns True and makes one POST."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        payload: ConversationCompletedPayload = {
            "session_id": "s",
            "transaction_id": "t",
            "model": "m",
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            "duration_ms": 100,
            "is_streaming": False,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        result = await sender._attempt_send(payload)
        assert result is True
        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_http_error_returns_false(sender):
    """4xx/5xx response returns False (will be retried)."""
    mock_response = MagicMock()
    mock_response.status_code = 503

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        payload: ConversationCompletedPayload = {
            "session_id": None,
            "transaction_id": "t",
            "model": "m",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "duration_ms": 0,
            "is_streaming": False,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        result = await sender._attempt_send(payload)
        assert result is False


@pytest.mark.asyncio
async def test_send_network_error_returns_false(sender):
    """Network errors return False (will be retried)."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value = mock_client

        payload: ConversationCompletedPayload = {
            "session_id": None,
            "transaction_id": "t",
            "model": "m",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "duration_ms": 0,
            "is_streaming": False,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        result = await sender._attempt_send(payload)
        assert result is False


@pytest.mark.asyncio
async def test_fire_and_forget_success(sender):
    """fire_and_forget dispatches a background task that succeeds."""
    with patch.object(sender, "_attempt_send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        sender.fire_and_forget(
            session_id="s",
            transaction_id="t",
            model="m",
            input_tokens=10,
            output_tokens=20,
            duration_ms=500,
            is_streaming=False,
        )
        # Allow the background task to run
        await asyncio.sleep(0.05)
        mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_fire_and_forget_retries_on_failure(sender_with_retries):
    """fire_and_forget retries up to max_retries on failure."""
    call_count = 0

    async def fail_twice_then_succeed(payload):
        nonlocal call_count
        call_count += 1
        return call_count >= 3  # Fail first 2, succeed on 3rd

    with patch.object(sender_with_retries, "_attempt_send", side_effect=fail_twice_then_succeed):
        sender_with_retries.fire_and_forget(
            session_id="s",
            transaction_id="t",
            model="m",
            input_tokens=10,
            output_tokens=20,
            duration_ms=500,
            is_streaming=False,
        )
        await asyncio.sleep(0.5)
        assert call_count == 3


@pytest.mark.asyncio
async def test_fire_and_forget_gives_up_after_max_retries(sender_with_retries):
    """fire_and_forget stops after max_retries exhausted."""
    call_count = 0

    async def always_fail(payload):
        nonlocal call_count
        call_count += 1
        return False

    with patch.object(sender_with_retries, "_attempt_send", side_effect=always_fail):
        sender_with_retries.fire_and_forget(
            session_id="s",
            transaction_id="t",
            model="m",
            input_tokens=10,
            output_tokens=20,
            duration_ms=500,
            is_streaming=False,
        )
        await asyncio.sleep(0.5)
        # max_retries=3 means 1 initial + 3 retries = 4 total attempts
        assert call_count == 4


@pytest.mark.asyncio
async def test_fire_and_forget_no_crash_on_exception(sender):
    """fire_and_forget does not propagate exceptions to caller."""

    async def raise_exception(payload):
        raise RuntimeError("unexpected error")

    with patch.object(sender, "_attempt_send", side_effect=raise_exception):
        # Should not raise
        sender.fire_and_forget(
            session_id="s",
            transaction_id="t",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
            is_streaming=False,
        )
        await asyncio.sleep(0.05)


# ── WebhookSender disabled (no URL) ───────────────────────────────────────────


def test_sender_disabled_when_no_url():
    """WebhookSender with no URL is disabled."""
    sender = WebhookSender(url=None)
    assert sender.enabled is False


def test_sender_enabled_when_url_set():
    """WebhookSender with URL is enabled."""
    sender = WebhookSender(url="https://example.com/hook")
    assert sender.enabled is True


@pytest.mark.asyncio
async def test_fire_and_forget_noop_when_disabled():
    """fire_and_forget does nothing when sender is disabled."""
    sender = WebhookSender(url=None)
    with patch.object(sender, "_attempt_send", new_callable=AsyncMock) as mock_send:
        sender.fire_and_forget(
            session_id="s",
            transaction_id="t",
            model="m",
            input_tokens=0,
            output_tokens=0,
            duration_ms=0,
            is_streaming=False,
        )
        await asyncio.sleep(0.05)
        mock_send.assert_not_called()
