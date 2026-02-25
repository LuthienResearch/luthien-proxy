
"""Unit tests for AnthropicPolicyInterface ABC."""

from abc import ABC
from unittest.mock import MagicMock

import pytest

from luthien_proxy.policy_core.anthropic_interface import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
)


class TestAnthropicPolicyInterface:
    """Tests for AnthropicPolicyInterface ABC."""

    def test_is_abstract_base_class(self):
        """AnthropicPolicyInterface should be an ABC."""
        assert issubclass(AnthropicPolicyInterface, ABC)

    def test_cannot_instantiate_directly(self):
        """Cannot instantiate AnthropicPolicyInterface directly."""
        with pytest.raises(TypeError, match="abstract"):
            AnthropicPolicyInterface()

    def test_requires_all_abstract_methods(self):
        """Subclass must implement all abstract methods."""

        # Partial implementation should fail
        class PartialPolicy(AnthropicPolicyInterface):
            async def on_anthropic_request(self, request, context):
                return request

        with pytest.raises(TypeError, match="abstract"):
            PartialPolicy()

    def test_can_implement_all_methods(self):
        """Subclass implementing all methods should be instantiable."""

        class CompletePolicy(AnthropicPolicyInterface):
            async def on_anthropic_request(self, request, context):
                return request

            async def on_anthropic_response(self, response, context):
                return response

            async def on_anthropic_stream_event(self, event, context):
                return [event]

        policy = CompletePolicy()
        assert isinstance(policy, AnthropicPolicyInterface)

    @pytest.mark.asyncio
    async def test_methods_are_async(self):
        """All interface methods should be async."""

        class TestPolicy(AnthropicPolicyInterface):
            async def on_anthropic_request(self, request, context):
                return request

            async def on_anthropic_response(self, response, context):
                return response

            async def on_anthropic_stream_event(self, event, context):
                return [event]

        policy = TestPolicy()
        mock_request = MagicMock()
        mock_context = MagicMock()
        mock_response = MagicMock()
        mock_event = MagicMock()

        # Verify async methods are awaitable
        result = await policy.on_anthropic_request(mock_request, mock_context)
        assert result is mock_request

        result = await policy.on_anthropic_response(mock_response, mock_context)
        assert result is mock_response

        result = await policy.on_anthropic_stream_event(mock_event, mock_context)
        assert result == [mock_event]

    @pytest.mark.asyncio
    async def test_stream_event_can_return_empty_list(self):
        """on_anthropic_stream_event can return [] to filter events."""

        class FilteringPolicy(AnthropicPolicyInterface):
            async def on_anthropic_request(self, request, context):
                return request

            async def on_anthropic_response(self, response, context):
                return response

            async def on_anthropic_stream_event(self, event, context):
                return []

        policy = FilteringPolicy()
        mock_event = MagicMock()
        mock_context = MagicMock()

        result = await policy.on_anthropic_stream_event(mock_event, mock_context)
        assert result == []

    def test_abstract_methods_list(self):
        """Verify the expected abstract methods are defined."""
        expected_methods = {
            "on_anthropic_request",
            "on_anthropic_response",
            "on_anthropic_stream_event",
        }
        assert AnthropicPolicyInterface.__abstractmethods__ == expected_methods


class TestAnthropicStreamEventExport:
    """Tests for AnthropicStreamEvent type alias."""

    def test_anthropic_stream_event_exported(self):
        """AnthropicStreamEvent should be exported from the module."""
        # Just verify it's importable and is a type alias
        from anthropic.lib.streaming import MessageStreamEvent

        assert AnthropicStreamEvent is MessageStreamEvent
