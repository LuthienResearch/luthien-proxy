"""Unit tests for llm/completion.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.llm.completion import CompletionResult, completion


class TestCompletionResult:
    def test_frozen(self):
        r = CompletionResult(text="hello", input_tokens=10, output_tokens=5)
        assert r.text == "hello"
        assert r.input_tokens == 10
        assert r.output_tokens == 5
        with pytest.raises(Exception):
            r.text = "other"  # type: ignore[misc]


class TestCompletion:
    @pytest.mark.asyncio
    async def test_basic_call(self):
        """System message is extracted and passed as Anthropic system param."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = "hello world"
        mock_message.usage.input_tokens = 10
        mock_message.usage.output_tokens = 5

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await completion(
                model="claude-haiku-4-5",
                messages=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hi"},
                ],
                api_key="test-key",
            )

        assert result.text == "hello world"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are helpful."
        assert call_kwargs["messages"] == [{"role": "user", "content": "Hi"}]
        assert call_kwargs["model"] == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_no_system_message(self):
        """When no system message, system param is not passed."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = "response"
        mock_message.usage.input_tokens = 5
        mock_message.usage.output_tokens = 3

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await completion(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "Hi"}],
                api_key="test-key",
            )

        call_kwargs = mock_client.messages.create.call_args[1]
        assert "system" not in call_kwargs

    @pytest.mark.asyncio
    async def test_passes_optional_params(self):
        """Temperature, max_tokens, extra_headers, base_url forwarded."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = "ok"
        mock_message.usage.input_tokens = 1
        mock_message.usage.output_tokens = 1

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ) as mock_constructor:
            await completion(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "Hi"}],
                api_key="test-key",
                base_url="https://custom.api.com",
                temperature=0.7,
                max_tokens=100,
                extra_headers={"x-custom": "val"},
            )

        mock_constructor.assert_called_once_with(api_key="test-key", base_url="https://custom.api.com")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 100
        assert call_kwargs["extra_headers"] == {"x-custom": "val"}

    @pytest.mark.asyncio
    async def test_empty_content_raises(self):
        """Raises ValueError when response has no text content blocks."""
        mock_message = MagicMock()
        mock_message.content = []

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            with pytest.raises(ValueError, match="No text content"):
                await completion(
                    model="claude-haiku-4-5",
                    messages=[{"role": "user", "content": "Hi"}],
                    api_key="test-key",
                )

    @pytest.mark.asyncio
    async def test_uses_env_api_key_when_none(self):
        """When api_key is None, AsyncAnthropic falls back to env var."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = "ok"
        mock_message.usage.input_tokens = 1
        mock_message.usage.output_tokens = 1

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ) as mock_constructor:
            await completion(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "Hi"}],
            )

        constructor_kwargs = mock_constructor.call_args[1]
        assert "api_key" not in constructor_kwargs

    @pytest.mark.asyncio
    async def test_client_closed_on_success(self):
        """Client is closed after successful call."""
        mock_message = MagicMock()
        mock_message.content = [MagicMock()]
        mock_message.content[0].text = "ok"
        mock_message.usage.input_tokens = 1
        mock_message.usage.output_tokens = 1

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        mock_client.close = AsyncMock()

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            await completion(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "Hi"}],
                api_key="test-key",
            )

        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_text_blocks_concatenated(self):
        """Multiple text blocks are joined with newlines."""
        block1 = MagicMock()
        block1.text = "first part"
        block2 = MagicMock()
        block2.text = "second part"

        mock_message = MagicMock()
        mock_message.content = [block1, block2]
        mock_message.usage.input_tokens = 10
        mock_message.usage.output_tokens = 8

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await completion(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "Hi"}],
                api_key="test-key",
            )

        assert result.text == "first part\nsecond part"

    @pytest.mark.asyncio
    async def test_client_closed_on_error(self):
        """Client is closed even when API call raises."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API error"))
        mock_client.close = AsyncMock()

        with patch(
            "luthien_proxy.llm.completion.anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            with pytest.raises(RuntimeError, match="API error"):
                await completion(
                    model="claude-haiku-4-5",
                    messages=[{"role": "user", "content": "Hi"}],
                    api_key="test-key",
                )

        mock_client.close.assert_called_once()
