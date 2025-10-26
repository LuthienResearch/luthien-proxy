"""Tests for V3 EventBasedPolicy.

This module tests the V3 event-based policy architecture including:
- PolicyContext with request and scratchpad
- StreamingContext with output_finished tracking
- EventBasedPolicy hook dispatch
- Chunk builder utilities
- Default forwarding behavior
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.event_based_noop import EventBasedNoOpPolicy
from luthien_proxy.v2.policies.event_based_policy import EventBasedPolicy, StreamingContext
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.v2.streaming.utils import build_block_chunk, build_text_chunk


class TestPolicyContext:
    """Tests for V3 PolicyContext with request and scratchpad."""

    def test_policy_context_includes_request(self):
        """PolicyContext should include request field."""
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])
            context = PolicyContext(
                call_id="test-123",
                span=span,
                request=request,
            )

            assert context.request == request
            assert context.request.model == "gpt-4"

    def test_policy_context_scratchpad_is_empty_dict(self):
        """PolicyContext.scratchpad should start as empty dict."""
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(
                call_id="test-123",
                span=span,
                request=request,
            )

            assert context.scratchpad == {}
            assert isinstance(context.scratchpad, dict)

    def test_policy_context_scratchpad_can_store_data(self):
        """PolicyContext.scratchpad should support arbitrary data storage."""
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(
                call_id="test-123",
                span=span,
                request=request,
            )

            # Store various types
            context.scratchpad["count"] = 0
            context.scratchpad["flag"] = True
            context.scratchpad["buffer"] = []
            context.scratchpad["metadata"] = {"key": "value"}

            assert context.scratchpad["count"] == 0
            assert context.scratchpad["flag"] is True
            assert context.scratchpad["buffer"] == []
            assert context.scratchpad["metadata"] == {"key": "value"}


class TestStreamingContext:
    """Tests for V3 StreamingContext with output_finished tracking."""

    @pytest.fixture
    def streaming_context(self):
        """Create a streaming context for testing."""
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            policy_context = PolicyContext(
                call_id="test-123",
                span=span,
                request=request,
            )
            outgoing = asyncio.Queue[ModelResponse]()
            return StreamingContext(
                policy_context=policy_context,
                keepalive=None,
                outgoing=outgoing,
            )

    @pytest.mark.asyncio
    async def test_send_text_convenience(self, streaming_context):
        """send_text should create and send a text chunk."""
        await streaming_context.send_text("Hello")

        chunk = await streaming_context._outgoing.get()
        assert chunk.choices[0].delta.get("content") == "Hello"  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_send_text_with_finish(self, streaming_context):
        """send_text with finish=True should mark output finished."""
        assert not streaming_context.is_output_finished()

        await streaming_context.send_text("Done", finish=True)

        assert streaming_context.is_output_finished()
        chunk = await streaming_context._outgoing.get()
        assert chunk.choices[0].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_send_after_output_finished_raises(self, streaming_context):
        """send() after output finished should raise RuntimeError."""
        streaming_context.mark_output_finished()

        with pytest.raises(RuntimeError, match="Cannot send chunks after output stream is finished"):
            await streaming_context.send(MagicMock(spec=ModelResponse))

    @pytest.mark.asyncio
    async def test_send_text_after_output_finished_raises(self, streaming_context):
        """send_text() after output finished should raise RuntimeError."""
        streaming_context.mark_output_finished()

        with pytest.raises(RuntimeError, match="Cannot send chunks after output stream is finished"):
            await streaming_context.send_text("Hello")

    def test_mark_output_finished(self, streaming_context):
        """mark_output_finished should set flag."""
        assert not streaming_context.is_output_finished()

        streaming_context.mark_output_finished()

        assert streaming_context.is_output_finished()


class TestChunkBuilders:
    """Tests for chunk builder utilities."""

    def test_build_text_chunk(self):
        """build_text_chunk should create valid ModelResponse."""
        chunk = build_text_chunk("Hello world", model="gpt-4")

        assert chunk.model == "gpt-4"
        assert chunk.choices[0].delta.get("content") == "Hello world"  # type: ignore[union-attr]
        assert chunk.choices[0].finish_reason is None

    def test_build_text_chunk_with_finish_reason(self):
        """build_text_chunk with finish_reason should set it."""
        chunk = build_text_chunk("Done", model="gpt-4", finish_reason="stop")

        assert chunk.choices[0].finish_reason == "stop"

    def test_build_block_chunk_content(self):
        """build_block_chunk should handle ContentStreamBlock."""
        block = ContentStreamBlock()
        block.content = "Hello world"
        block.is_complete = True

        chunk = build_block_chunk(block, model="gpt-4")

        assert chunk.choices[0].delta.get("content") == "Hello world"  # type: ignore[union-attr]

    def test_build_block_chunk_tool_call(self):
        """build_block_chunk should handle ToolCallStreamBlock."""
        block = ToolCallStreamBlock(id="call_123", index=0)
        block.name = "get_weather"
        block.arguments = '{"location": "NYC"}'
        block.is_complete = True

        chunk = build_block_chunk(block, model="gpt-4")

        delta = chunk.choices[0].delta
        # Delta can be dict or Delta object - handle both
        if hasattr(delta, "tool_calls"):
            tool_calls = delta.tool_calls
        else:
            tool_calls = delta.get("tool_calls")  # type: ignore[union-attr]

        assert tool_calls is not None
        assert len(tool_calls) == 1
        tc = tool_calls[0]
        # Handle both dict and object forms
        tc_id = tc["id"] if isinstance(tc, dict) else tc.id
        tc_index = tc["index"] if isinstance(tc, dict) else tc.index
        tc_func = tc["function"] if isinstance(tc, dict) else tc.function
        tc_name = tc_func["name"] if isinstance(tc_func, dict) else tc_func.name
        tc_args = tc_func["arguments"] if isinstance(tc_func, dict) else tc_func.arguments

        assert tc_id == "call_123"
        assert tc_index == 0
        assert tc_name == "get_weather"
        assert tc_args == '{"location": "NYC"}'


class TestEventBasedNoOpPolicy:
    """Tests for V3 NoOp policy with default implementations."""

    @pytest.mark.asyncio
    async def test_process_request_passthrough(self):
        """NoOp policy should pass request through unchanged."""
        policy = EventBasedNoOpPolicy()
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])
            context = PolicyContext(call_id="test-123", span=span, request=request)

            result = await policy.process_request(request, context)

            assert result == request

    @pytest.mark.asyncio
    async def test_process_full_response_passthrough(self):
        """NoOp policy should pass response through unchanged."""
        policy = EventBasedNoOpPolicy()
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(call_id="test-123", span=span, request=request)
            response = MagicMock(spec=ModelResponse)

            result = await policy.process_full_response(response, context)

            assert result == response

    @pytest.mark.asyncio
    async def test_process_streaming_forwards_content(self):
        """NoOp policy should forward content chunks."""
        policy = EventBasedNoOpPolicy()
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(call_id="test-123", span=span, request=request)

            # Create queues
            incoming = asyncio.Queue[ModelResponse]()
            outgoing = asyncio.Queue[ModelResponse]()

            # Send content chunks
            chunk1 = build_text_chunk("Hello ", model="gpt-4")
            chunk2 = build_text_chunk("world", model="gpt-4", finish_reason="stop")
            await incoming.put(chunk1)
            await incoming.put(chunk2)
            incoming.shutdown()

            # Process
            await policy.process_streaming_response(incoming, outgoing, context)

            # Verify forwarding
            # The default policy forwards:
            # 1. First content delta ("Hello ")
            # 2. Second content delta ("world")
            # 3. Finish reason chunk (empty content with finish_reason="stop")
            out1 = await outgoing.get()
            out2 = await outgoing.get()
            out3 = await outgoing.get()

            # Should have forwarded content
            assert out1.choices[0].delta.get("content") == "Hello "  # type: ignore[union-attr]
            assert out2.choices[0].delta.get("content") == "world"  # type: ignore[union-attr]
            # Third chunk is finish-only (no content field when text is empty)
            assert out3.choices[0].delta.get("content") is None  # type: ignore[union-attr]
            assert out3.choices[0].finish_reason == "stop"


class TestEventBasedPolicyHooks:
    """Tests for EventBasedPolicy hook invocation and customization."""

    @pytest.mark.asyncio
    async def test_on_content_delta_called_for_each_delta(self):
        """on_content_delta should be called for each content delta."""

        class TrackingPolicy(EventBasedPolicy):
            def __init__(self):
                self.deltas: list[str] = []

            async def on_content_delta(self, delta, block, context, streaming_ctx):
                self.deltas.append(delta)
                # Don't forward - just track
                pass

        policy = TrackingPolicy()
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(call_id="test-123", span=span, request=request)

            incoming = asyncio.Queue[ModelResponse]()
            outgoing = asyncio.Queue[ModelResponse]()

            # Send content chunks (finish_reason comes in separate chunk)
            await incoming.put(build_text_chunk("Hello ", model="gpt-4"))
            await incoming.put(build_text_chunk("world", model="gpt-4"))
            await incoming.put(build_text_chunk("", model="gpt-4", finish_reason="stop"))
            incoming.shutdown()

            # Process
            await policy.process_streaming_response(incoming, outgoing, context)

            # Should have tracked both deltas (not the empty finish chunk)
            assert policy.deltas == ["Hello ", "world"]

    @pytest.mark.asyncio
    async def test_on_content_complete_called_once(self):
        """on_content_complete should be called once when block finishes."""

        class CompletionTrackingPolicy(EventBasedPolicy):
            def __init__(self):
                self.complete_content: str | None = None

            async def on_content_complete(self, block, context, streaming_ctx):
                self.complete_content = block.content

        policy = CompletionTrackingPolicy()
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(call_id="test-123", span=span, request=request)

            incoming = asyncio.Queue[ModelResponse]()
            outgoing = asyncio.Queue[ModelResponse]()

            # Send content chunks (finish_reason in separate chunk)
            await incoming.put(build_text_chunk("Hello ", model="gpt-4"))
            await incoming.put(build_text_chunk("world", model="gpt-4"))
            await incoming.put(build_text_chunk("", model="gpt-4", finish_reason="stop"))
            incoming.shutdown()

            # Process
            await policy.process_streaming_response(incoming, outgoing, context)

            # Should have complete content
            assert policy.complete_content == "Hello world"

    @pytest.mark.asyncio
    async def test_scratchpad_accessible_in_hooks(self):
        """Scratchpad should be accessible and mutable across hooks."""

        class ScratchpadPolicy(EventBasedPolicy):
            async def on_stream_start(self, context, streaming_ctx):
                context.scratchpad["counter"] = 0

            async def on_content_delta(self, delta, block, context, streaming_ctx):
                context.scratchpad["counter"] += 1

            async def on_stream_complete(self, context):
                # Counter should reflect number of deltas
                pass

        policy = ScratchpadPolicy()
        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[])
            context = PolicyContext(call_id="test-123", span=span, request=request)

            incoming = asyncio.Queue[ModelResponse]()
            outgoing = asyncio.Queue[ModelResponse]()

            # Send 3 content chunks (finish_reason in separate chunk)
            await incoming.put(build_text_chunk("A", model="gpt-4"))
            await incoming.put(build_text_chunk("B", model="gpt-4"))
            await incoming.put(build_text_chunk("C", model="gpt-4"))
            await incoming.put(build_text_chunk("", model="gpt-4", finish_reason="stop"))
            incoming.shutdown()

            # Process
            await policy.process_streaming_response(incoming, outgoing, context)

            # Should have counted 3 deltas (not the empty finish chunk)
            assert context.scratchpad["counter"] == 3
