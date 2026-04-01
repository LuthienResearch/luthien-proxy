"""Unit tests for HumanizerPolicy streaming and non-streaming behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from anthropic.types import (
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
)
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.llm.types.anthropic import (
    AnthropicResponse,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.humanizer_policy import HumanizerPolicy
from luthien_proxy.policies.humanizer_utils import HumanizerConfig


@pytest.fixture()
def config() -> HumanizerConfig:
    return HumanizerConfig(
        model="test-model",
        api_key="test-key",
        temperature=0.7,
        min_text_length=10,
        chunk_size=50,
        force_chunk_size=150,
        context_overlap=20,
    )


@pytest.fixture()
def policy(config: HumanizerConfig) -> HumanizerPolicy:
    with patch("luthien_proxy.policies.humanizer_policy.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.llm_judge_model = None
        settings.llm_judge_api_base = None
        settings.llm_judge_api_key = None
        settings.litellm_master_key = None
        return HumanizerPolicy(config=config)


@pytest.fixture()
def context() -> PolicyContext:
    return PolicyContext.for_testing()


def _make_response(text: str) -> AnthropicResponse:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_TEST_MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _text_start(index: int = 0) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start", index=index, content_block=TextBlock(type="text", text="")
    )


def _text_delta(text: str, index: int = 0) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent.model_construct(
        type="content_block_delta",
        index=index,
        delta=TextDelta.model_construct(type="text_delta", text=text),
    )


def _text_stop(index: int = 0) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


class TestHumanizerPolicyBasic:
    def test_short_policy_name(self, policy: HumanizerPolicy):
        assert policy.short_policy_name == "Humanizer"


class TestHumanizerNonStreaming:
    @pytest.mark.asyncio()
    async def test_humanizes_text_in_chunks(self, policy: HumanizerPolicy, context: PolicyContext):
        """Non-streaming text is split into chunks and humanized."""
        # Two paragraphs separated by \n\n, each > chunk_size (50)
        para1 = "This is a vibrant testament to the crucial landscape of innovation and progress. " * 2
        para2 = "Additionally, this comprehensive tapestry showcases the holistic paradigm of synergy. " * 2
        text = f"{para1}\n\n{para2}"
        response = _make_response(text)

        call_count = 0

        async def fake_chunk(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            chunk_text = args[0]
            return chunk_text.replace("vibrant", "lively").replace("Additionally", "Also")

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", side_effect=fake_chunk):
            result = await policy.on_anthropic_response(response, context)

        assert call_count >= 1
        result_text = result["content"][0]["text"]
        assert "vibrant" not in result_text or "lively" in result_text

    @pytest.mark.asyncio()
    async def test_skips_short_text(self, policy: HumanizerPolicy, context: PolicyContext):
        response = _make_response("OK")

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", new_callable=AsyncMock) as mock:
            result = await policy.on_anthropic_response(response, context)

        mock.assert_not_called()
        assert result["content"][0]["text"] == "OK"

    @pytest.mark.asyncio()
    async def test_tool_blocks_pass_through(self, policy: HumanizerPolicy, context: PolicyContext):
        response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": DEFAULT_TEST_MODEL,
            "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {"cmd": "ls"}}],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", new_callable=AsyncMock) as mock:
            result = await policy.on_anthropic_response(response, context)

        mock.assert_not_called()
        assert result["content"][0]["name"] == "bash"

    @pytest.mark.asyncio()
    async def test_chunk_error_falls_back(self, policy: HumanizerPolicy, context: PolicyContext):
        text = "A " * 100  # Long enough to trigger humanization
        response = _make_response(text)

        with patch(
            "luthien_proxy.policies.humanizer_policy.call_humanizer_chunk",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM down"),
        ):
            result = await policy.on_anthropic_response(response, context)

        # Should fall back to original text
        assert result["content"][0]["text"] == text


class TestHumanizerStreaming:
    @pytest.mark.asyncio()
    async def test_short_text_emitted_on_stop(self, policy: HumanizerPolicy, context: PolicyContext):
        """Short text (< chunk_size) is buffered and emitted on block stop."""
        await policy.on_anthropic_stream_event(_text_start(), context)
        result = await policy.on_anthropic_stream_event(_text_delta("Hello world."), context)
        assert result == []  # buffered

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", new_callable=AsyncMock) as mock:
            mock.return_value = "Hello world."
            result = await policy.on_anthropic_stream_event(_text_stop(), context)

        # Should get the text delta + stop
        assert len(result) >= 2
        delta_evt = result[0]
        assert isinstance(delta_evt, RawContentBlockDeltaEvent)
        assert isinstance(delta_evt.delta, TextDelta)

    @pytest.mark.asyncio()
    async def test_paragraph_boundary_triggers_chunk(self, policy: HumanizerPolicy, context: PolicyContext):
        """Text emitted when paragraph boundary found after chunk_size."""
        await policy.on_anthropic_stream_event(_text_start(), context)

        # Feed text longer than chunk_size (50) with a paragraph boundary
        long_para = "x" * 60 + "\n\n"
        remainder = "more text after break"

        chunk_calls: list[str] = []

        async def capture_chunk(*args, **kwargs):
            chunk_calls.append(args[0])
            return f"[humanized:{len(args[0])}]"

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", side_effect=capture_chunk):
            result = await policy.on_anthropic_stream_event(_text_delta(long_para + remainder), context)

        # The first paragraph should have been extracted and humanized
        assert len(chunk_calls) == 1
        assert chunk_calls[0] == long_para
        assert len(result) == 1
        assert isinstance(result[0], RawContentBlockDeltaEvent)

    @pytest.mark.asyncio()
    async def test_force_split_without_paragraph(self, policy: HumanizerPolicy, context: PolicyContext):
        """Very long text without \\n\\n is force-split at force_chunk_size."""
        await policy.on_anthropic_stream_event(_text_start(), context)

        # Feed text longer than force_chunk_size (150) with no \n\n
        long_text = "word " * 40  # 200 chars, no \n\n

        chunk_calls: list[str] = []

        async def capture_chunk(*args, **kwargs):
            chunk_calls.append(args[0])
            return args[0]

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", side_effect=capture_chunk):
            result = await policy.on_anthropic_stream_event(_text_delta(long_text), context)

        assert len(chunk_calls) >= 1
        assert len(result) >= 1

    @pytest.mark.asyncio()
    async def test_code_fence_prevents_split(self, policy: HumanizerPolicy, context: PolicyContext):
        """Text inside a code fence is not split even at paragraph boundaries."""
        await policy.on_anthropic_stream_event(_text_start(), context)

        # Open a code fence, then add text > chunk_size with \n\n
        fenced = "```python\n" + "x" * 60 + "\n\n" + "y" * 30

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", new_callable=AsyncMock) as mock:
            result = await policy.on_anthropic_stream_event(_text_delta(fenced), context)

        # Should NOT have split — fence is still open
        mock.assert_not_called()
        assert result == []

    @pytest.mark.asyncio()
    async def test_context_overlap_passed(self, policy: HumanizerPolicy, context: PolicyContext):
        """Second chunk receives tail of first chunk's humanized output as context."""
        await policy.on_anthropic_stream_event(_text_start(), context)

        para1 = "x" * 60 + "\n\n"
        para2 = "y" * 60 + "\n\n"

        call_args_list: list[dict] = []

        async def capture_chunk(*args, **kwargs):
            call_args_list.append(kwargs)
            return f"humanized_{len(call_args_list)}"

        with patch("luthien_proxy.policies.humanizer_policy.call_humanizer_chunk", side_effect=capture_chunk):
            await policy.on_anthropic_stream_event(_text_delta(para1 + para2), context)

        assert len(call_args_list) == 2
        # First chunk has no previous context
        assert call_args_list[0]["previous_context"] == ""
        # Second chunk has tail of first humanized output
        assert call_args_list[1]["previous_context"] != ""

    @pytest.mark.asyncio()
    async def test_flush_on_message_delta(self, policy: HumanizerPolicy, context: PolicyContext):
        """Buffer is flushed before message_delta event."""
        await policy.on_anthropic_stream_event(_text_start(), context)
        await policy.on_anthropic_stream_event(_text_delta("buffered text here"), context)

        msg_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},  # type: ignore[arg-type]
            usage=MessageDeltaUsage(output_tokens=10),
        )

        with patch(
            "luthien_proxy.policies.humanizer_policy.call_humanizer_chunk",
            new_callable=AsyncMock,
            return_value="flushed",
        ):
            result = await policy.on_anthropic_stream_event(msg_delta, context)

        # Should have flush delta + message_delta
        assert len(result) == 2
        assert isinstance(result[0], RawContentBlockDeltaEvent)
        assert isinstance(result[1], RawMessageDeltaEvent)

    @pytest.mark.asyncio()
    async def test_error_falls_back_to_original(self, policy: HumanizerPolicy, context: PolicyContext):
        """If humanizer fails for a chunk, original text is emitted."""
        await policy.on_anthropic_stream_event(_text_start(), context)

        long_para = "x" * 60 + "\n\n"

        with patch(
            "luthien_proxy.policies.humanizer_policy.call_humanizer_chunk",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM error"),
        ):
            result = await policy.on_anthropic_stream_event(_text_delta(long_para), context)

        assert len(result) == 1
        delta_evt = result[0]
        assert isinstance(delta_evt, RawContentBlockDeltaEvent)
        assert isinstance(delta_evt.delta, TextDelta)
        assert delta_evt.delta.text == long_para


class TestHumanizerConfigDefaults:
    def test_default_chunk_size(self):
        config = HumanizerConfig(model="test")
        assert config.chunk_size == 500

    def test_default_force_chunk_size(self):
        config = HumanizerConfig(model="test")
        assert config.force_chunk_size == 1500

    def test_default_context_overlap(self):
        config = HumanizerConfig(model="test")
        assert config.context_overlap == 200
