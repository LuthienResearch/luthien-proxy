# ABOUTME: Unit tests for OpenAIPolicyInterface ABC

"""Unit tests for OpenAIPolicyInterface ABC."""

from abc import ABC
from unittest.mock import MagicMock

import pytest

from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface


class TestOpenAIPolicyInterface:
    """Tests for OpenAIPolicyInterface ABC."""

    def test_is_abstract_base_class(self):
        """OpenAIPolicyInterface should be an ABC."""
        assert issubclass(OpenAIPolicyInterface, ABC)

    def test_cannot_instantiate_directly(self):
        """Cannot instantiate OpenAIPolicyInterface directly."""
        with pytest.raises(TypeError, match="abstract"):
            OpenAIPolicyInterface()

    def test_requires_all_abstract_methods(self):
        """Subclass must implement all abstract methods."""

        # Partial implementation should fail
        class PartialPolicy(OpenAIPolicyInterface):
            async def on_openai_request(self, request, context):
                return request

        with pytest.raises(TypeError, match="abstract"):
            PartialPolicy()

    def test_can_implement_all_methods(self):
        """Subclass implementing all methods should be instantiable."""

        class CompletePolicy(OpenAIPolicyInterface):
            async def on_openai_request(self, request, context):
                return request

            async def on_openai_response(self, response, context):
                return response

            async def on_chunk_received(self, ctx):
                pass

            async def on_content_delta(self, ctx):
                pass

            async def on_content_complete(self, ctx):
                pass

            async def on_tool_call_delta(self, ctx):
                pass

            async def on_tool_call_complete(self, ctx):
                pass

            async def on_finish_reason(self, ctx):
                pass

            async def on_stream_complete(self, ctx):
                pass

            async def on_streaming_policy_complete(self, ctx):
                pass

        policy = CompletePolicy()
        assert isinstance(policy, OpenAIPolicyInterface)

    @pytest.mark.asyncio
    async def test_methods_are_async(self):
        """All interface methods should be async."""

        class TestPolicy(OpenAIPolicyInterface):
            async def on_openai_request(self, request, context):
                return request

            async def on_openai_response(self, response, context):
                return response

            async def on_chunk_received(self, ctx):
                pass

            async def on_content_delta(self, ctx):
                pass

            async def on_content_complete(self, ctx):
                pass

            async def on_tool_call_delta(self, ctx):
                pass

            async def on_tool_call_complete(self, ctx):
                pass

            async def on_finish_reason(self, ctx):
                pass

            async def on_stream_complete(self, ctx):
                pass

            async def on_streaming_policy_complete(self, ctx):
                pass

        policy = TestPolicy()
        mock_request = MagicMock()
        mock_context = MagicMock()
        mock_response = MagicMock()
        mock_streaming_ctx = MagicMock()

        # Verify async methods are awaitable
        result = await policy.on_openai_request(mock_request, mock_context)
        assert result is mock_request

        result = await policy.on_openai_response(mock_response, mock_context)
        assert result is mock_response

        # Streaming hooks return None
        await policy.on_chunk_received(mock_streaming_ctx)
        await policy.on_content_delta(mock_streaming_ctx)
        await policy.on_content_complete(mock_streaming_ctx)
        await policy.on_tool_call_delta(mock_streaming_ctx)
        await policy.on_tool_call_complete(mock_streaming_ctx)
        await policy.on_finish_reason(mock_streaming_ctx)
        await policy.on_stream_complete(mock_streaming_ctx)
        await policy.on_streaming_policy_complete(mock_streaming_ctx)

    def test_abstract_methods_list(self):
        """Verify the expected abstract methods are defined."""
        expected_methods = {
            "on_openai_request",
            "on_openai_response",
            "on_chunk_received",
            "on_content_delta",
            "on_content_complete",
            "on_tool_call_delta",
            "on_tool_call_complete",
            "on_finish_reason",
            "on_stream_complete",
            "on_streaming_policy_complete",
        }
        assert OpenAIPolicyInterface.__abstractmethods__ == expected_methods
