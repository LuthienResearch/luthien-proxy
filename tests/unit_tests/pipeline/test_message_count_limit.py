"""Unit tests for message count limit validation.

Tests that both OpenAI and Anthropic request pipelines reject requests
with too many messages, while allowing normal-sized requests through.

Uses monkeypatched MAX_MESSAGE_COUNT (set to 5) to avoid allocating
10,000-element lists in tests.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from luthien_proxy.pipeline.anthropic_processor import (
    _process_request as anthropic_process_request,
)
from luthien_proxy.pipeline.processor import (
    _process_request as openai_process_request,
)

TEST_LIMIT = 5


@pytest.fixture(autouse=True)
def _small_message_limit(monkeypatch):
    """Patch MAX_MESSAGE_COUNT to a small value in both processor modules."""
    monkeypatch.setattr("luthien_proxy.pipeline.processor.MAX_MESSAGE_COUNT", TEST_LIMIT)
    monkeypatch.setattr("luthien_proxy.pipeline.anthropic_processor.MAX_MESSAGE_COUNT", TEST_LIMIT)


@pytest.fixture
def mock_emitter():
    return MagicMock()


@pytest.fixture
def mock_tracer_ctx():
    """Patch the tracer context manager for both processor modules.

    Yields a (openai_patcher, anthropic_patcher) tuple; each test class
    uses the one it needs via the pipeline-specific fixtures below.
    """
    span = MagicMock()
    span.set_attribute = MagicMock()
    span.add_event = MagicMock()

    def _make_patcher(module_path: str):
        patcher = patch(f"{module_path}.tracer")
        mock_tracer = patcher.start()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        return patcher

    openai_patcher = _make_patcher("luthien_proxy.pipeline.processor")
    anthropic_patcher = _make_patcher("luthien_proxy.pipeline.anthropic_processor")
    yield
    openai_patcher.stop()
    anthropic_patcher.stop()


class TestOpenAIMessageCountLimit:
    """Tests for message count validation in the OpenAI pipeline."""

    @pytest.fixture
    def mock_request(self):
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/chat/completions"
        return request

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self, mock_request, mock_emitter, mock_tracer_ctx):
        """Request exceeding MAX_MESSAGE_COUNT is rejected with 400."""
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(TEST_LIMIT + 1)]
        mock_request.json = AsyncMock(return_value={"model": "gpt-4", "messages": messages})

        with pytest.raises(HTTPException) as exc_info:
            await openai_process_request(request=mock_request, call_id="test", emitter=mock_emitter)

        assert exc_info.value.status_code == 400
        assert str(TEST_LIMIT) in exc_info.value.detail
        assert "messages" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_allows_at_limit(self, mock_request, mock_emitter, mock_tracer_ctx):
        """Request with exactly MAX_MESSAGE_COUNT messages is allowed."""
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(TEST_LIMIT)]
        mock_request.json = AsyncMock(return_value={"model": "gpt-4", "messages": messages})

        request_message, _raw, _session = await openai_process_request(
            request=mock_request, call_id="test", emitter=mock_emitter
        )
        assert request_message.model == "gpt-4"

    @pytest.mark.asyncio
    async def test_allows_normal_count(self, mock_request, mock_emitter, mock_tracer_ctx):
        """Single-message request passes through."""
        mock_request.json = AsyncMock(
            return_value={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        )

        request_message, _raw, _session = await openai_process_request(
            request=mock_request, call_id="test", emitter=mock_emitter
        )
        assert request_message.model == "gpt-4"


class TestAnthropicMessageCountLimit:
    """Tests for message count validation in the Anthropic pipeline."""

    @pytest.fixture
    def mock_request(self):
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        return request

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self, mock_request, mock_emitter, mock_tracer_ctx):
        """Anthropic request exceeding MAX_MESSAGE_COUNT is rejected with 400."""
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(TEST_LIMIT + 1)]
        mock_request.json = AsyncMock(
            return_value={"model": "claude-haiku-4-5-20251001", "messages": messages, "max_tokens": 1024}
        )

        with pytest.raises(HTTPException) as exc_info:
            await anthropic_process_request(request=mock_request, call_id="test", emitter=mock_emitter)

        assert exc_info.value.status_code == 400
        assert str(TEST_LIMIT) in exc_info.value.detail
        assert "messages" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_allows_at_limit(self, mock_request, mock_emitter, mock_tracer_ctx):
        """Anthropic request with exactly MAX_MESSAGE_COUNT messages is allowed."""
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(TEST_LIMIT)]
        mock_request.json = AsyncMock(
            return_value={"model": "claude-haiku-4-5-20251001", "messages": messages, "max_tokens": 1024}
        )

        anthropic_request, _raw, _session = await anthropic_process_request(
            request=mock_request, call_id="test", emitter=mock_emitter
        )
        assert anthropic_request["model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_allows_normal_count(self, mock_request, mock_emitter, mock_tracer_ctx):
        """Single-message Anthropic request passes through."""
        mock_request.json = AsyncMock(
            return_value={
                "model": "claude-haiku-4-5-20251001",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
            }
        )

        anthropic_request, _raw, _session = await anthropic_process_request(
            request=mock_request, call_id="test", emitter=mock_emitter
        )
        assert anthropic_request["model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "messages_value",
        [
            pytest.param(None, id="null"),
            pytest.param("not-a-list", id="string"),
            pytest.param(42, id="integer"),
        ],
    )
    async def test_non_list_messages_not_rejected(self, mock_request, mock_emitter, mock_tracer_ctx, messages_value):
        """Non-list messages field should not trigger the count check.

        The validation only applies to list-typed messages. Other types
        will fail downstream validation, not the count check.
        """
        mock_request.json = AsyncMock(
            return_value={
                "model": "claude-haiku-4-5-20251001",
                "messages": messages_value,
                "max_tokens": 1024,
            }
        )

        # Should NOT raise HTTPException with 400 for message count.
        # May raise other errors downstream, but not our count check.
        try:
            await anthropic_process_request(request=mock_request, call_id="test", emitter=mock_emitter)
        except HTTPException as e:
            # If it raises, it should NOT be our message count error
            assert "exceeding maximum" not in e.detail
