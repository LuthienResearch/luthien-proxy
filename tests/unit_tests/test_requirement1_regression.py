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

        assert len(results) == len(events)
        text_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, TextDelta)
        ]
        assert len(text_deltas) == len(delta_texts)
        for i, td in enumerate(text_deltas):
            assert td.delta.text == delta_texts[i]

    @pytest.mark.asyncio
    async def test_streaming_event_count_matches_input(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test for #59/#61: SSE output event count must match input.

        Full pipeline test through _handle_streaming.
        """
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_count_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
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
                delta=TextDelta.model_construct(type="text_delta", text="Test"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 5},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        async def mock_stream(request: Any) -> AsyncIterator[AnthropicStreamEvent]:
            for e in events:
                yield e

        mock_client = MagicMock()
        mock_client.stream = mock_stream

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        mock_root_span = MagicMock()

        response = await _handle_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            call_id="test-count",
            root_span=mock_root_span,
        )

        assert isinstance(response, FastAPIStreamingResponse)

        sse_events = []
        async for chunk in response.body_iterator:
            sse_events.append(chunk)

        assert len(sse_events) == len(events)


# =============================================================================
# 7. Image content blocks must pass through (#94, #103, #108)
# =============================================================================


class TestImageContentPassthrough:
    """Regression tests for image content block handling.

    Bugs #94, #103, #108: Requests with base64 image content blocks
    crashed or were corrupted by the proxy. Images must pass through
    unchanged.
    """

    @pytest.mark.asyncio
    async def test_base64_image_survives_pipeline(self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock) -> None:
        """Regression test for #94: base64 images must not crash the pipeline.

        Request with base64 image goes through _handle_non_streaming and
        the image data reaches upstream intact.
        """
        image_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data,
                            },
                        },
                    ],
                }
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
            call_id="test-base64-image",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        content = forwarded["messages"][0]["content"]
        image_block = content[1]
        assert image_block["type"] == "image"
        assert image_block["source"]["type"] == "base64"
        assert image_block["source"]["data"] == image_data

    @pytest.mark.asyncio
    async def test_url_image_survives_pipeline(self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock) -> None:
        """Regression test for #103: URL images must pass through the pipeline."""
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this."},
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/image.png",
                            },
                        },
                    ],
                }
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
            call_id="test-url-image",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        image_block = forwarded["messages"][0]["content"][1]
        assert image_block["source"]["url"] == "https://example.com/image.png"

    @pytest.mark.asyncio
    async def test_multiple_images_survive_pipeline(self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock) -> None:
        """Regression test for #94: multiple images in one message must all reach upstream."""
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Compare these images."},
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "image1_data"},
                        },
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "image2_data"},
                        },
                    ],
                }
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
            call_id="test-multi-image",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        image_blocks = [b for b in forwarded["messages"][0]["content"] if b["type"] == "image"]
        assert len(image_blocks) == 2


# =============================================================================
# 8. Tool definitions must remain unique (#208)
# =============================================================================


class TestToolDefinitionUniqueness:
    """Regression tests for tool definition handling.

    Bug #208: After /compact processing, tool definitions could get
    duplicated. The proxy must not introduce duplicate tools.
    """

    @pytest.mark.asyncio
    async def test_tools_forwarded_exactly_to_upstream(self, anthropic_client: AnthropicClient) -> None:
        """Regression test for #208: tool definitions must be forwarded without modification."""
        tools = [
            {
                "name": "search",
                "description": "Search the web",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "name": "calculate",
                "description": "Do math",
                "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}},
            },
        ]
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Help me."}],
            "max_tokens": 1024,
            "tools": tools,
        }

        kwargs = anthropic_client._prepare_request_kwargs(request)

        assert kwargs["tools"] is tools  # Same reference, not copied
        assert len(kwargs["tools"]) == 2

    @pytest.mark.asyncio
    async def test_large_tool_set_survives_pipeline(self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock) -> None:
        """Regression test for #208: large tool sets must survive the full pipeline.

        Claude Code sessions can have 20+ tool definitions. Verify they
        all reach upstream without duplication or loss.
        """
        tools = [
            {
                "name": f"tool_{i}",
                "description": f"Tool number {i}",
                "input_schema": {"type": "object", "properties": {f"arg_{i}": {"type": "string"}}},
            }
            for i in range(25)
        ]

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Do something."}],
            "max_tokens": 1024,
            "tools": tools,
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
            call_id="test-large-toolset",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        forwarded_names = [t["name"] for t in forwarded["tools"]]
        assert len(forwarded_names) == 25
        assert len(set(forwarded_names)) == 25
        for i in range(25):
            assert forwarded_names[i] == f"tool_{i}"


# =============================================================================
# Cross-cutting: Full pipeline integration (streaming)
# =============================================================================


class TestFullPipelineStreaming:
    """Cross-cutting regression tests that verify the full streaming pipeline."""

    @pytest.mark.asyncio
    async def test_tool_use_streaming_passes_through(self, noop_policy: NoOpPolicy, policy_ctx: PolicyContext) -> None:
        """Regression test: tool_use streaming events must pass through intact."""
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_tool_stream",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": TEST_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 100, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=ToolUseBlock.model_construct(
                    type="tool_use", id="toolu_test_123", name="read_file", input={}
                ),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"path": "/tmp/test.txt"}'),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "tool_use", "stop_sequence": None},
                usage={"output_tokens": 20},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        executor = AnthropicStreamExecutor()
        results = []
        async for result in executor.process(async_iter_from_list(events), noop_policy, policy_ctx):
            results.append(result)

        assert len(results) == len(events)

        tool_start = results[1]
        assert isinstance(tool_start, RawContentBlockStartEvent)
        assert tool_start.content_block.type == "tool_use"
        assert tool_start.content_block.name == "read_file"

        json_delta = results[2]
        assert isinstance(json_delta, RawContentBlockDeltaEvent)
        assert isinstance(json_delta.delta, InputJSONDelta)
        assert '"/tmp/test.txt"' in json_delta.delta.partial_json

    @pytest.mark.asyncio
    async def test_mixed_content_and_tool_use_streaming(
        self, noop_policy: NoOpPolicy, policy_ctx: PolicyContext
    ) -> None:
        """Regression test: mixed text + tool_use streams must pass through."""
        events: list[AnthropicStreamEvent] = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_mixed",
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
                content_block=TextBlock.model_construct(type="text", text=""),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="Let me read that file."),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=1,
                content_block=ToolUseBlock.model_construct(
                    type="tool_use", id="toolu_mixed_456", name="read_file", input={}
                ),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=1,
                delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"path": "test.py"}'),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=1),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "tool_use", "stop_sequence": None},
                usage={"output_tokens": 30},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        executor = AnthropicStreamExecutor()
        results = []
        async for result in executor.process(async_iter_from_list(events), noop_policy, policy_ctx):
            results.append(result)

        assert len(results) == len(events)
        text_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, TextDelta)
        ]
        json_deltas = [
            r for r in results if isinstance(r, RawContentBlockDeltaEvent) and isinstance(r.delta, InputJSONDelta)
        ]
        assert len(text_deltas) == 1
        assert len(json_deltas) == 1
        assert text_deltas[0].delta.text == "Let me read that file."


# =============================================================================
# Cross-cutting: Complex real-world request through pipeline
# =============================================================================


class TestRequestPassthroughIntegrity:
    """Cross-cutting tests that verify the proxy does not alter requests."""

    @pytest.mark.asyncio
    async def test_complex_real_world_request_survives_pipeline(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test: a realistic Claude Code request must pass through the pipeline intact.

        This simulates a real-world request with system blocks, tools,
        cache_control, images, and tool results all combined, sent through
        _handle_non_streaming to verify nothing is dropped or corrupted.
        """
        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read this file and analyze the screenshot."},
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANSUhEUg=="},
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I'll read the file."},
                        {"type": "tool_use", "id": "toolu_abc", "name": "read_file", "input": {"path": "/tmp/test.py"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_abc", "content": "print('hello world')"},
                    ],
                },
                {"role": "assistant", "content": "The file contains a hello world program."},
                {"role": "user", "content": "Now explain it."},
            ],
            "max_tokens": 8192,
            "system": [
                {
                    "type": "text",
                    "text": "You are a helpful coding assistant.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file from disk",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            "temperature": 0.3,
            "metadata": {"user_id": "test_user_session_abc123"},
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
            call_id="test-complex",
        )

        assert isinstance(response, JSONResponse)
        forwarded = mock_client.complete.call_args[0][0]
        assert forwarded["model"] == TEST_MODEL
        assert forwarded["max_tokens"] == 8192
        assert forwarded["temperature"] == 0.3
        assert len(forwarded["messages"]) == 5
        assert len(forwarded["tools"]) == 1
        assert forwarded["tools"][0]["cache_control"]["type"] == "ephemeral"
        assert forwarded["system"][0]["cache_control"]["type"] == "ephemeral"

    @pytest.mark.asyncio
    async def test_response_with_all_content_types_preserved(
        self, noop_policy: NoOpPolicy, mock_policy_ctx: MagicMock
    ) -> None:
        """Regression test: response with thinking + text + tool_use must be preserved.

        The pipeline must return all content block types in the JSON response
        without dropping or reordering any of them.
        """
        response_data: AnthropicResponse = {
            "id": "msg_mixed_content",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me think...", "signature": "sig_abc"},
                {"type": "text", "text": "Here is my analysis."},
                {"type": "tool_use", "id": "toolu_xyz", "name": "search", "input": {"query": "test"}},
            ],
            "model": TEST_MODEL,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 200},
        }

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=response_data)
        mock_emitter = MagicMock()

        request: AnthropicRequest = {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Analyze this."}],
            "max_tokens": 8192,
        }

        response = await _handle_non_streaming(
            final_request=request,
            policy=noop_policy,
            policy_ctx=mock_policy_ctx,
            anthropic_client=mock_client,
            emitter=mock_emitter,
            call_id="test-content-types",
        )

        assert isinstance(response, JSONResponse)
        body = json.loads(response.body.decode())
        content_types = [b["type"] for b in body["content"]]
        assert content_types == ["thinking", "text", "tool_use"]
