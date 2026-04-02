"""Unit tests for DeAIPolicy streaming and non-streaming behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from anthropic.types import (
    InputJSONDelta,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.llm.types.anthropic import (
    AnthropicResponse,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.deai_policy import DeAIPolicy
from luthien_proxy.policies.deai_utils import DeAIConfig


@pytest.fixture()
def config() -> DeAIConfig:
    return DeAIConfig(
        model="test-model",
        api_key="test-key",
        temperature=0.7,
        min_text_length=10,
        chunk_size=50,
        force_chunk_size=150,
        context_overlap=20,
    )


@pytest.fixture()
def policy(config: DeAIConfig) -> DeAIPolicy:
    with patch("luthien_proxy.policies.deai_policy.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.llm_judge_model = None
        settings.llm_judge_api_base = None
        settings.llm_judge_api_key = None
        settings.litellm_master_key = None
        return DeAIPolicy(config=config)


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


def _tool_start(index: int = 1) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id="toolu_123", name="bash", input={}),
    )


def _text_delta(text: str, index: int = 0) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent.model_construct(
        type="content_block_delta",
        index=index,
        delta=TextDelta.model_construct(type="text_delta", text=text),
    )


def _tool_delta(json: str, index: int = 1) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent.model_construct(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json=json),
    )


def _block_stop(index: int = 0) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


class TestDeAIPolicyBasic:
    def test_short_policy_name(self, policy: DeAIPolicy):
        assert policy.short_policy_name == "DeAI"


class TestDeAINonStreaming:
    @pytest.mark.asyncio()
    async def test_humanizes_text_in_chunks(self, policy: DeAIPolicy, context: PolicyContext):
        para1 = "This is a vibrant testament to the crucial landscape of innovation and progress. " * 2
        para2 = "Additionally, this comprehensive tapestry showcases the holistic paradigm of synergy. " * 2
        text = f"{para1}\n\n{para2}"
        response = _make_response(text)

        call_count = 0

        async def fake_chunk(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return args[0].replace("vibrant", "lively").replace("Additionally", "Also")

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", side_effect=fake_chunk):
            result = await policy.on_anthropic_response(response, context)

        assert call_count >= 1
        result_text = result["content"][0]["text"]
        assert "vibrant" not in result_text or "lively" in result_text

    @pytest.mark.asyncio()
    async def test_skips_short_text(self, policy: DeAIPolicy, context: PolicyContext):
        response = _make_response("OK")

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", new_callable=AsyncMock) as mock:
            result = await policy.on_anthropic_response(response, context)

        mock.assert_not_called()
        assert result["content"][0]["text"] == "OK"

    @pytest.mark.asyncio()
    async def test_tool_blocks_pass_through(self, policy: DeAIPolicy, context: PolicyContext):
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

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", new_callable=AsyncMock) as mock:
            result = await policy.on_anthropic_response(response, context)

        mock.assert_not_called()
        assert result["content"][0]["name"] == "bash"

    @pytest.mark.asyncio()
    async def test_chunk_error_falls_back(self, policy: DeAIPolicy, context: PolicyContext):
        text = "A " * 100
        response = _make_response(text)

        with patch(
            "luthien_proxy.policies.deai_policy.call_deai_chunk",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM down"),
        ):
            result = await policy.on_anthropic_response(response, context)

        assert result["content"][0]["text"] == text


class TestDeAIStreaming:
    @pytest.mark.asyncio()
    async def test_short_text_emitted_on_stop(self, policy: DeAIPolicy, context: PolicyContext):
        await policy.on_anthropic_stream_event(_text_start(), context)
        result = await policy.on_anthropic_stream_event(_text_delta("Hello world."), context)
        assert result == []

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", new_callable=AsyncMock) as mock:
            mock.return_value = "Hello world."
            result = await policy.on_anthropic_stream_event(_block_stop(), context)

        assert len(result) >= 2
        assert isinstance(result[0], RawContentBlockDeltaEvent)
        assert isinstance(result[0].delta, TextDelta)

    @pytest.mark.asyncio()
    async def test_paragraph_boundary_triggers_chunk(self, policy: DeAIPolicy, context: PolicyContext):
        await policy.on_anthropic_stream_event(_text_start(), context)

        long_para = "x" * 60 + "\n\n"
        remainder = "more text after break"

        chunk_calls: list[str] = []

        async def capture_chunk(*args, **kwargs):
            chunk_calls.append(args[0])
            return f"[humanized:{len(args[0])}]"

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", side_effect=capture_chunk):
            result = await policy.on_anthropic_stream_event(_text_delta(long_para + remainder), context)

        assert len(chunk_calls) == 1
        assert chunk_calls[0] == long_para
        assert len(result) == 1
        assert isinstance(result[0], RawContentBlockDeltaEvent)

    @pytest.mark.asyncio()
    async def test_force_split_without_paragraph(self, policy: DeAIPolicy, context: PolicyContext):
        await policy.on_anthropic_stream_event(_text_start(), context)

        long_text = "word " * 40  # 200 chars, no \n\n

        chunk_calls: list[str] = []

        async def capture_chunk(*args, **kwargs):
            chunk_calls.append(args[0])
            return args[0]

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", side_effect=capture_chunk):
            result = await policy.on_anthropic_stream_event(_text_delta(long_text), context)

        assert len(chunk_calls) >= 1
        assert len(result) >= 1

    @pytest.mark.asyncio()
    async def test_context_overlap_passed(self, policy: DeAIPolicy, context: PolicyContext):
        await policy.on_anthropic_stream_event(_text_start(), context)

        para1 = "x" * 60 + "\n\n"
        para2 = "y" * 60 + "\n\n"

        call_args_list: list[dict] = []

        async def capture_chunk(*args, **kwargs):
            call_args_list.append(kwargs)
            return f"humanized_{len(call_args_list)}"

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", side_effect=capture_chunk):
            await policy.on_anthropic_stream_event(_text_delta(para1 + para2), context)

        assert len(call_args_list) == 2
        assert call_args_list[0]["previous_context"] == ""
        assert call_args_list[1]["previous_context"] != ""

    @pytest.mark.asyncio()
    async def test_flush_on_message_delta(self, policy: DeAIPolicy, context: PolicyContext):
        await policy.on_anthropic_stream_event(_text_start(), context)
        await policy.on_anthropic_stream_event(_text_delta("buffered text here"), context)

        msg_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},  # type: ignore[arg-type]
            usage=MessageDeltaUsage(output_tokens=10),
        )

        with patch(
            "luthien_proxy.policies.deai_policy.call_deai_chunk",
            new_callable=AsyncMock,
            return_value="flushed",
        ):
            result = await policy.on_anthropic_stream_event(msg_delta, context)

        assert len(result) == 2
        assert isinstance(result[0], RawContentBlockDeltaEvent)
        assert isinstance(result[1], RawMessageDeltaEvent)

    @pytest.mark.asyncio()
    async def test_error_falls_back_to_original(self, policy: DeAIPolicy, context: PolicyContext):
        await policy.on_anthropic_stream_event(_text_start(), context)

        long_para = "x" * 60 + "\n\n"

        with patch(
            "luthien_proxy.policies.deai_policy.call_deai_chunk",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM error"),
        ):
            result = await policy.on_anthropic_stream_event(_text_delta(long_para), context)

        assert len(result) == 1
        assert isinstance(result[0].delta, TextDelta)
        assert result[0].delta.text == long_para

    @pytest.mark.asyncio()
    async def test_tool_use_stop_does_not_flush_text(self, policy: DeAIPolicy, context: PolicyContext):
        """Tool-use block stop should not flush text buffer or pop state."""
        # Start text block, buffer some text
        await policy.on_anthropic_stream_event(_text_start(index=0), context)
        await policy.on_anthropic_stream_event(_text_delta("buffered prose ", index=0), context)

        # Start and stop a tool_use block (index=1)
        await policy.on_anthropic_stream_event(_tool_start(index=1), context)
        await policy.on_anthropic_stream_event(_tool_delta('{"cmd":"ls"}', index=1), context)

        with patch("luthien_proxy.policies.deai_policy.call_deai_chunk", new_callable=AsyncMock) as mock:
            result = await policy.on_anthropic_stream_event(_block_stop(index=1), context)

        # Tool stop should pass through without calling humanizer
        mock.assert_not_called()
        assert len(result) == 1
        assert isinstance(result[0], RawContentBlockStopEvent)

        # Text buffer should still be intact — verify by stopping text block
        with patch(
            "luthien_proxy.policies.deai_policy.call_deai_chunk",
            new_callable=AsyncMock,
            return_value="humanized prose",
        ) as mock:
            result = await policy.on_anthropic_stream_event(_block_stop(index=0), context)

        mock.assert_called_once()
        assert len(result) == 2  # delta + stop
        assert isinstance(result[0], RawContentBlockDeltaEvent)
        assert isinstance(result[0].delta, TextDelta)
        assert result[0].delta.text == "humanized prose"


class TestDeAIConfigDefaults:
    def test_default_chunk_size(self):
        config = DeAIConfig(model="test")
        assert config.chunk_size == 500

    def test_default_force_chunk_size(self):
        config = DeAIConfig(model="test")
        assert config.force_chunk_size == 1500

    def test_default_context_overlap(self):
        config = DeAIConfig(model="test")
        assert config.context_overlap == 200
