"""Regression test suite for Uber Requirement 1: "Invisible Unless Needed".

The proxy must not break requests that work fine without it. This file contains
regression tests for every known bug where the proxy violated this requirement.
Each test is named after the bug it prevents and includes a docstring linking
to the original issue.

These tests exercise real pipeline code with mocked upstream APIs to verify
that the fixes for each historical bug remain in place.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolUseBlock,
)
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse

from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
)
from luthien_proxy.pipeline.anthropic_processor import (
    _handle_non_streaming,
    _handle_streaming,
)
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_core.anthropic_interface import AnthropicStreamEvent
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.anthropic_executor import AnthropicStreamExecutor

TEST_MODEL = "claude-haiku-4-5-20251001"

# =============================================================================
# Shared Fixtures
# =============================================================================


@pytest.fixture
def noop_policy() -> NoOpPolicy:
    """A NoOp policy that passes everything through unchanged."""
    return NoOpPolicy()


@pytest.fixture
def policy_ctx() -> PolicyContext:
    """A PolicyContext suitable for unit tests."""
    return PolicyContext.for_testing()


@pytest.fixture
def mock_policy_ctx() -> MagicMock:
    """A mock PolicyContext for pipeline-level tests that need MagicMock."""
    ctx = MagicMock()
    ctx.session_id = None
    ctx.response_summary = None
    ctx.request_summary = None
    return ctx


@pytest.fixture
def anthropic_client() -> AnthropicClient:
    """An AnthropicClient for testing _prepare_request_kwargs."""
    return AnthropicClient(api_key="test-key-not-used")


@pytest.fixture(autouse=True)
def _mock_tracer():
    """Patch the tracer context manager for both processor modules."""
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


async def async_iter_from_list(items: list[Any]) -> AsyncIterator[Any]:
    """Convert a list to an async iterator."""
    for item in items:
        yield item


def _make_echo_response(request: AnthropicRequest) -> AnthropicResponse:
    """Create a minimal valid AnthropicResponse for pipeline tests.

    Returns a response that mirrors the request model, so pipeline-level
    tests can verify the request reached upstream intact.
    """
    return AnthropicResponse(
        id="msg_test",
        type="message",
        role="assistant",
        content=[{"type": "text", "text": "OK"}],
        model=request["model"],
        stop_reason="end_turn",
        stop_sequence=None,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


# =============================================================================
# 1. Thinking blocks must pass through (#128, #129)
# =============================================================================


class TestThinkingBlockPassthrough:
    """Regression tests for thinking block passthrough.

    Bugs #128 and #129: Extended thinking blocks were being dropped or
    corrupted when passing through the proxy. The proxy must preserve
    thinking blocks in both streaming and non-streaming responses.
    """

    @pytest.mark.asyncio
    async def test_thinking_config_forwarded_to_upstream(self, anthropic_client: AnthropicClient) -> None:
        """Regression test for #128: thinking config must be forwarded to Anthropic API.

        The AnthropicClient._prepare_request_kwargs must include 'thinking'
        when present in the request, so the upstream API receives it.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Think step by step."}],
            "max_tokens": 16000,
            "thinking": {"type": "enabled", "budget_tokens": 5000},
        }
        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert "thinking" in kwargs
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] == 5000

    @pytest.mark.asyncio
    async def test_thinking_blocks_in_non_streaming_response(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #128: thinking blocks must appear in non-streaming responses.

        When the upstream returns a response with thinking content blocks,
        the proxy must include them in the response sent to the client.
        """
        response_with_thinking: AnthropicResponse = {
            "id": "msg_thinking_test",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me think about this...", "signature": "sig123"},
                {"type": "text", "text": "Here is my answer."},
            ],
            "model": TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 50, "output_tokens": 100},
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=response_with_thinking)
        mock_emitter = MagicMock()

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Think about this."}],
            "max_tokens": 16000,
            "thinking": {"type": "enabled", "budget_tokens": 5000},
        }

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-thinking",
        )

        assert isinstance(response, JSONResponse)
        body = json.loads(response.body.decode())
        content_types = [block["type"] for block in body["content"]]
        assert "thinking" in content_types
        assert "text" in content_types

        thinking_block = next(b for b in body["content"] if b["type"] == "thinking")
        assert thinking_block["thinking"] == "Let me think about this..."

    @pytest.mark.asyncio
    async def test_thinking_events_stream_through(self, noop_policy: NoOpPolicy, policy_ctx: PolicyContext) -> None:
        """Regression test for #129: thinking events must stream through unchanged.

        The AnthropicStreamExecutor with NoOp policy must pass thinking-related
        streaming events to the client without dropping or corrupting them.
        """
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_thinking_stream",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 50, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=ThinkingBlock.model_construct(type="thinking", thinking="", signature=""),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=ThinkingDelta.model_construct(type="thinking_delta", thinking="Step 1: analyze..."),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=1,
                content_block=TextBlock.model_construct(type="text", text=""),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=1,
                delta=TextDelta.model_construct(type="text_delta", text="The answer is 42."),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=1),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 50},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        executor = AnthropicStreamExecutor()
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, noop_policy, policy_ctx):
            results.append(result)

        assert len(results) == len(events)

        thinking_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, ThinkingDelta)
        ]
        assert len(thinking_deltas) == 1
        assert thinking_deltas[0].delta.thinking == "Step 1: analyze..."

        text_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, TextDelta)
        ]
        assert len(text_deltas) == 1
        assert text_deltas[0].delta.text == "The answer is 42."


# =============================================================================
# 2. cache_control fields must not cause 400s (#178)
# =============================================================================


class TestCacheControlSanitization:
    """Regression tests for cache_control field handling.

    Bug #178: Requests with cache_control on tools or system messages
    caused 400 errors. The proxy must preserve cache_control fields
    when forwarding to the upstream API.
    """

    @pytest.mark.asyncio
    async def test_cache_control_on_tools_passes_through(self, anthropic_client: AnthropicClient) -> None:
        """Regression test for #178: cache_control on tools must not be stripped.

        Tools with cache_control: {type: "ephemeral"} are valid Anthropic API
        requests for prompt caching. The proxy must forward them as-is.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Use the tool."}],
            "max_tokens": 1024,
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file from disk",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert "tools" in kwargs
        assert len(kwargs["tools"]) == 1
        assert "cache_control" in kwargs["tools"][0]
        assert kwargs["tools"][0]["cache_control"]["type"] == "ephemeral"

    @pytest.mark.asyncio
    async def test_cache_control_on_system_messages_passes_through(self, anthropic_client: AnthropicClient) -> None:
        """Regression test for #178: cache_control on system blocks must be preserved."""
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "system": [
                {
                    "type": "text",
                    "text": "You are a helpful assistant.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert "system" in kwargs
        system_blocks = kwargs["system"]
        assert len(system_blocks) == 1
        assert "cache_control" in system_blocks[0]
        assert system_blocks[0]["cache_control"]["type"] == "ephemeral"

    @pytest.mark.asyncio
    async def test_cache_control_survives_full_pipeline(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #178: cache_control must survive the full non-streaming pipeline.

        Request with cache_control on tools and system blocks goes through
        _handle_non_streaming and the response is returned without error.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Analyze this code.",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ],
            "max_tokens": 1024,
            "system": [
                {
                    "type": "text",
                    "text": "You are a code reviewer.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=_make_echo_response(request))
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-cache-control",
        )

        assert isinstance(response, JSONResponse)
        # Verify the request was forwarded with cache_control intact
        forwarded_request = mock_client.complete.call_args[0][0]
        assert forwarded_request["tools"][0]["cache_control"]["type"] == "ephemeral"
        assert forwarded_request["system"][0]["cache_control"]["type"] == "ephemeral"


# =============================================================================
# 3. Empty text content blocks must be filtered (#201)
# =============================================================================


class TestEmptyTextBlockFiltering:
    """Regression tests for empty text content block handling.

    Bug #201: The pipeline produced empty text blocks like
    {"type": "text", "content": ""} which caused 400 errors from the
    Anthropic API. The StreamingChunkAssembler strips empty content
    during the tool call phase.
    """

    @pytest.mark.asyncio
    async def test_empty_content_stripped_during_tool_call_phase(self) -> None:
        """Regression test for #201: empty delta.content must be stripped during tool calls."""
        from litellm.types.utils import Delta, ModelResponse, StreamingChoices

        from luthien_proxy.streaming.streaming_chunk_assembler import StreamingChunkAssembler

        chunks_received: list[ModelResponse] = []

        async def capture_chunk(chunk: ModelResponse, state: Any, context: Any) -> None:
            chunks_received.append(chunk)

        assembler = StreamingChunkAssembler(on_chunk_callback=capture_chunk)

        content_chunk = ModelResponse(
            id="test",
            created=1234567890,
            model=TEST_MODEL,
            object="chat.completion.chunk",
            choices=[StreamingChoices(index=0, delta=Delta(content="Hello"), finish_reason=None)],
        )

        tool_call_chunk = ModelResponse(
            id="test",
            created=1234567890,
            model=TEST_MODEL,
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        content="",
                        tool_calls=[
                            {
                                "index": 0,
                                "id": "tool_123",
                                "function": {"name": "read_file", "arguments": '{"path":'},
                            }
                        ],
                    ),
                    finish_reason=None,
                )
            ],
        )

        empty_content_chunk = ModelResponse(
            id="test",
            created=1234567890,
            model=TEST_MODEL,
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(
                        content="",
                        tool_calls=[{"index": 0, "function": {"arguments": '"/foo"}'}}],
                    ),
                    finish_reason=None,
                )
            ],
        )

        finish_chunk = ModelResponse(
            id="test",
            created=1234567890,
            model=TEST_MODEL,
            object="chat.completion.chunk",
            choices=[StreamingChoices(index=0, delta=Delta(), finish_reason="tool_calls")],
        )

        async def make_stream() -> AsyncIterator[ModelResponse]:
            yield content_chunk
            yield tool_call_chunk
            yield empty_content_chunk
            yield finish_chunk

        await assembler.process(make_stream(), context=None)

        for chunk in chunks_received[1:3]:
            delta = chunk.choices[0].delta  # type: ignore[union-attr]
            assert delta.content is None, (
                f"Empty content should be stripped during tool call phase, got: {delta.content!r}"
            )


# =============================================================================
# 4. Client parameters must be stripped before forwarding (#151)
# =============================================================================


class TestClientParameterStripping:
    """Regression tests for client parameter handling.

    Bug #151: Client-only parameters like context_management were forwarded
    to the Anthropic API, causing 400 errors. The proxy must strip unknown
    parameters.
    """

    @pytest.mark.asyncio
    async def test_unknown_params_not_forwarded_to_anthropic(self, anthropic_client: AnthropicClient) -> None:
        """Regression test for #151: client-only params must not reach upstream."""
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "context_management": {"enabled": True},  # type: ignore[typeddict-unknown-key]
            "custom_client_field": "should_be_dropped",  # type: ignore[typeddict-unknown-key]
        }
        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert "context_management" not in kwargs
        assert "custom_client_field" not in kwargs
        assert kwargs["model"] == TEST_MODEL
        assert kwargs["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_all_valid_optional_params_forwarded(self, anthropic_client: AnthropicClient) -> None:
        """Regression test: all valid Anthropic params must still be forwarded."""
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40,
            "stop_sequences": ["END"],
            "metadata": {"user_id": "test_user"},
            "thinking": {"type": "enabled", "budget_tokens": 5000},
        }
        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert kwargs["temperature"] == 0.7
        assert kwargs["top_p"] == 0.9
        assert kwargs["top_k"] == 40
        assert kwargs["stop_sequences"] == ["END"]
        assert kwargs["metadata"] == {"user_id": "test_user"}
        assert kwargs["thinking"]["type"] == "enabled"

    def test_openai_request_model_allows_extra_fields(self) -> None:
        """Regression test for #151: Request model must accept extra fields without error."""
        from luthien_proxy.llm.types.openai import Request

        req = Request(
            model=TEST_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=1024,
            context_management={"enabled": True},  # type: ignore[call-arg]
        )
        assert req.model == TEST_MODEL

    def test_litellm_drop_params_is_enabled(self) -> None:
        """Regression test for #151: litellm.drop_params must be True."""
        import litellm

        import luthien_proxy.llm.litellm_client  # noqa: F401

        assert litellm.drop_params is True


# =============================================================================
# 5. Orphaned tool_results must be handled gracefully (#167)
# =============================================================================


class TestOrphanedToolResults:
    """Regression tests for orphaned tool_result handling.

    Bug #167: After /compact, tool_result blocks could reference tool_use IDs
    that no longer exist in the conversation. The proxy must handle these
    without crashing.
    """

    @pytest.mark.asyncio
    async def test_orphaned_tool_result_survives_pipeline(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #167: orphaned tool_results must pass through the pipeline.

        When Claude Code does /compact, it can remove assistant messages
        containing tool_use blocks while leaving the corresponding user
        tool_result blocks. The full pipeline must handle this without crashing.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {"role": "user", "content": "Read the file."},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_orphaned_123",
                            "content": "file contents here",
                        }
                    ],
                },
                {"role": "assistant", "content": "I see the file contents."},
                {"role": "user", "content": "Now summarize it."},
            ],
            "max_tokens": 1024,
        }

        response_data = _make_echo_response(request)
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=response_data)
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-orphan",
        )

        assert isinstance(response, JSONResponse)
        # Verify full message array including orphaned tool_result was forwarded
        forwarded = mock_client.complete.call_args[0][0]
        assert len(forwarded["messages"]) == 4
        assert forwarded["messages"][1]["content"][0]["type"] == "tool_result"


# =============================================================================
# 6. Streaming must not duplicate events (#59, #61)
# =============================================================================


class TestStreamingDeduplication:
    """Regression tests for streaming event deduplication.

    Bugs #59 and #61: The proxy was duplicating message_start events
    and content deltas in streaming responses. Each event from upstream
    must be emitted exactly once to the client.
    """

    @pytest.mark.asyncio
    async def test_message_start_not_duplicated(self, noop_policy: NoOpPolicy, policy_ctx: PolicyContext) -> None:
        """Regression test for #59: message_start must appear exactly once."""
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_dedup_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=TextBlock.model_construct(type="text", text=""),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="Hello"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 5},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        executor = AnthropicStreamExecutor()
        results = []
        async for result in executor.process(async_iter_from_list(events), noop_policy, policy_ctx):
            results.append(result)

        message_starts = [r for r in results if isinstance(r, RawMessageStartEvent)]
        assert len(message_starts) == 1, f"Expected 1 message_start, got {len(message_starts)}"

    @pytest.mark.asyncio
    async def test_content_deltas_not_duplicated(self, noop_policy: NoOpPolicy, policy_ctx: PolicyContext) -> None:
        """Regression test for #61: content deltas must not be duplicated."""
        delta_texts = ["Hello", " ", "world", "!"]
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_delta_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=TextBlock.model_construct(type="text", text=""),
            ),
        ]
        for text in delta_texts:
            events.append(
                RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta",
                    index=0,
                    delta=TextDelta.model_construct(type="text_delta", text=text),
                )
            )
        events.extend(
            [
                RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
                RawMessageDeltaEvent.model_construct(
                    type="message_delta",
                    delta={"stop_reason": "end_turn", "stop_sequence": None},
                    usage={"output_tokens": 5},
                ),
                RawMessageStopEvent.model_construct(type="message_stop"),
            ]
        )

        executor = AnthropicStreamExecutor()
        results = []
        async for result in executor.process(async_iter_from_list(events), noop_policy, policy_ctx):
            results.append(result)

        text_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, TextDelta)
        ]
        assert len(text_deltas) == len(delta_texts)

        for expected_text, actual_event in zip(delta_texts, text_deltas):
            assert actual_event.delta.text == expected_text


# =============================================================================
# 7. Tool calls must not be mangled during streaming (#165)
# =============================================================================


class TestToolCallStreamingIntegrity:
    """Regression tests for tool call streaming integrity.

    Bug #165: Tool call arguments were being corrupted during streaming,
    with partial JSON being sent to clients. The proxy must accumulate
    tool call arguments correctly.
    """

    @pytest.mark.asyncio
    async def test_tool_call_arguments_assembled_correctly(self, noop_policy: NoOpPolicy) -> None:
        """Regression test for #165: tool call arguments must be assembled without corruption.

        The AnthropicStreamExecutor must properly accumulate tool_use deltas
        so clients receive complete, valid JSON arguments.
        """
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_tool_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=ToolUseBlock.model_construct(
                    type="tool_use", id="tool_123", name="read_file", input={}
                ),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"path": '),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='"/home/user/'),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='test.txt"}'),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "tool_use", "stop_sequence": None},
                usage={"output_tokens": 15},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        executor = AnthropicStreamExecutor()
        results = []
        async for result in executor.process(async_iter_from_list(events), noop_policy, PolicyContext.for_testing()):
            results.append(result)

        assert len(results) == len(events)

        tool_use_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, InputJSONDelta)
        ]

        assert len(tool_use_deltas) == 3
        full_json = "".join(delta.delta.partial_json for delta in tool_use_deltas)
        assert full_json == '{"path": "/home/user/test.txt"}'

        # Verify it's valid JSON
        import json

        parsed = json.loads(full_json)
        assert parsed["path"] == "/home/user/test.txt"


# =============================================================================
# 8. Message role sequences must be preserved (#118)
# =============================================================================


class TestMessageRoleSequences:
    """Regression tests for message role sequence preservation.

    Bug #118: The proxy was rejecting valid role sequences like
    user -> user -> assistant, which are allowed by the Anthropic API.
    The proxy must forward role sequences as-is.
    """

    @pytest.mark.asyncio
    async def test_consecutive_user_messages_allowed(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #118: consecutive user messages must be allowed.

        The Anthropic API accepts user -> user -> assistant sequences.
        The proxy must not reject or modify these sequences.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {"role": "user", "content": "First question"},
                {"role": "user", "content": "Follow-up question"},
                {"role": "assistant", "content": "Combined answer"},
                {"role": "user", "content": "Another question"},
            ],
            "max_tokens": 1024,
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=_make_echo_response(request))
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-roles",
        )

        assert isinstance(response, JSONResponse)
        # Verify the exact role sequence was forwarded
        forwarded = mock_client.complete.call_args[0][0]
        roles = [msg["role"] for msg in forwarded["messages"]]
        assert roles == ["user", "user", "assistant", "user"]

    @pytest.mark.asyncio
    async def test_assistant_without_prior_user_message_allowed(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #118: assistant-first messages must be allowed.

        Some applications need to prime the conversation with an assistant
        message before the first user message. This is valid for Anthropic API.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {"role": "assistant", "content": "I'm ready to help."},
                {"role": "user", "content": "What can you do?"},
            ],
            "max_tokens": 1024,
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=_make_echo_response(request))
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-assistant-first",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        roles = [msg["role"] for msg in forwarded["messages"]]
        assert roles == ["assistant", "user"]


# =============================================================================
# 9. Response format must be preserved (#185)
# =============================================================================


class TestResponseFormatPreservation:
    """Regression tests for response format preservation.

    Bug #185: Structured output requests using response_format were
    being stripped by the proxy. The proxy must forward response_format
    to ensure structured outputs work correctly.
    """

    @pytest.mark.asyncio
    async def test_json_response_format_preserved(self, anthropic_client: AnthropicClient) -> None:
        """Regression test for #185: JSON response_format must be forwarded.

        Requests with response_format: {type: "json"} must reach the
        Anthropic API so structured outputs work correctly.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Return JSON with name and age"}],
            "max_tokens": 1024,
            "response_format": {"type": "json"},
        }
        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert "response_format" in kwargs
        assert kwargs["response_format"]["type"] == "json"

    @pytest.mark.asyncio
    async def test_response_format_survives_pipeline(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #185: response_format must survive the full pipeline."""
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Generate a JSON response"}],
            "max_tokens": 1024,
            "response_format": {"type": "json"},
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=_make_echo_response(request))
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-response-format",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        assert "response_format" in forwarded
        assert forwarded["response_format"]["type"] == "json"


# =============================================================================
# 10. Streaming responses must have correct content-type (#144)
# =============================================================================


class TestStreamingContentType:
    """Regression tests for streaming content-type headers.

    Bug #144: Streaming responses were not setting the correct
    content-type header, causing client parsing issues.
    """

    @pytest.mark.asyncio
    async def test_streaming_response_has_correct_content_type(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #144: streaming responses must have text/event-stream content-type."""

        async def mock_stream():
            yield RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_stream_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            )
            yield RawMessageStopEvent.model_construct(type="message_stop")

        mock_client = MagicMock()
        mock_client.stream_complete = AsyncMock(return_value=mock_stream())
        mock_emitter = MagicMock()

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": True,
        }

        response = await _handle_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-content-type",
        )

        assert isinstance(response, FastAPIStreamingResponse)
        assert response.media_type == "text/event-stream"
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "no-cache"


# =============================================================================
# 11. Large message arrays must not cause memory errors (#122)
# =============================================================================


class TestLargeMessageArrays:
    """Regression tests for large message array handling.

    Bug #122: Very long conversations caused memory errors during
    processing. The proxy must handle large message arrays efficiently.
    """

    @pytest.mark.asyncio
    async def test_large_message_array_processes_successfully(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #122: large message arrays must not cause memory errors.

        Conversations with hundreds of messages should be processed without
        memory exhaustion or performance degradation.
        """
        # Create a large message array (100 exchanges = 200 messages)
        messages = []
        for i in range(100):
            messages.extend(
                [
                    {"role": "user", "content": f"Question {i}"},
                    {"role": "assistant", "content": f"Answer {i}"},
                ]
            )
        messages.append({"role": "user", "content": "Final question"})

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": messages,
            "max_tokens": 1024,
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=_make_echo_response(request))
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-large-array",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        assert len(forwarded["messages"]) == 201  # 100 * 2 + 1
        assert forwarded["messages"][-1]["content"] == "Final question"


# =============================================================================
# 12. Proxy must handle backend 429 rate limits gracefully (#133)
# =============================================================================


class TestRateLimitHandling:
    """Regression tests for rate limit handling.

    Bug #133: Backend 429 errors were not being forwarded correctly,
    causing clients to receive generic 500 errors instead of proper
    rate limit responses.
    """

    @pytest.mark.asyncio
    async def test_backend_429_forwarded_as_429(self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock) -> None:
        """Regression test for #133: backend 429s must be forwarded as 429s.

        When the upstream API returns a 429 rate limit error, the proxy
        must forward it as a 429, not convert it to a 500.
        """
        from anthropic import RateLimitError

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(
            side_effect=RateLimitError("Rate limit exceeded", response=MagicMock(status_code=429), body=None)
        )
        mock_emitter = MagicMock()

        with pytest.raises(RateLimitError):
            await _handle_non_streaming(
                final_request=request,
                policy=noop_policy,
                policy_ctx=mock_policy_ctx,
                anthropic_client=mock_client,
                emitter=mock_emitter,
                call_id="test-rate-limit",
            )


# =============================================================================
# 13. Empty assistant messages must not cause validation errors (#156)
# =============================================================================


class TestEmptyAssistantMessages:
    """Regression tests for empty assistant message handling.

    Bug #156: Assistant messages with empty content arrays were causing
    validation errors. The proxy must allow empty assistant messages
    as they are valid in certain contexts.
    """

    @pytest.mark.asyncio
    async def test_empty_assistant_message_allowed(self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock) -> None:
        """Regression test for #156: empty assistant messages must be allowed.

        Assistant messages with empty content arrays are valid when they
        contain only tool_use blocks or in certain conversation flows.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {"role": "user", "content": "Make a tool call"},
                {"role": "assistant", "content": []},  # Empty content array
                {"role": "user", "content": "Thank you"},
            ],
            "max_tokens": 1024,
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=_make_echo_response(request))
        mock_emitter = MagicMock()

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-empty-assistant",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        assert len(forwarded["messages"]) == 3
        assert forwarded["messages"][1]["content"] == []