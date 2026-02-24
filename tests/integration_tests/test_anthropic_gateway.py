"""Integration tests for the Anthropic gateway endpoint.

These tests verify the end-to-end flow through the /v1/messages endpoint
using the native Anthropic path, with the Anthropic SDK mocked.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from tests.constants import DEFAULT_CLAUDE_TEST_MODEL

from luthien_proxy.dependencies import Dependencies
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.litellm_client import LiteLLMClient
from luthien_proxy.observability.emitter import NullEventEmitter
from luthien_proxy.policy_manager import PolicyManager


@pytest.fixture
def mock_policy_manager():
    """Create a mock PolicyManager that returns NoOp policy."""
    manager = MagicMock(spec=PolicyManager)
    from luthien_proxy.policies.noop_policy import NoOpPolicy

    manager.current_policy = NoOpPolicy()
    return manager


@pytest.fixture
def mock_anthropic_client():
    """Create a mock AnthropicClient with a complete response.

    Note: The complete method should return an AnthropicResponse TypedDict,
    not a Pydantic Message, because the AnthropicClient.complete() method
    internally converts the SDK response to the TypedDict format.
    """
    from luthien_proxy.llm.types.anthropic import AnthropicResponse

    response: AnthropicResponse = {
        "id": "msg_test123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello from Claude!"}],
        "model": DEFAULT_CLAUDE_TEST_MODEL,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    client = MagicMock(spec=AnthropicClient)
    client._base_url = None
    client.complete = AsyncMock(return_value=response)
    return client


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = MagicMock(spec=Redis)
    redis.ping = AsyncMock(return_value=True)
    redis.publish = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def test_app(mock_policy_manager, mock_anthropic_client, mock_redis):
    """Create a test app with mocked dependencies."""
    from fastapi import FastAPI

    from luthien_proxy.gateway_routes import router

    # Create dependencies with mocks
    # Note: anthropic_policy comes from policy_manager.current_policy via get_anthropic_policy()
    deps = Dependencies(
        db_pool=None,
        redis_client=mock_redis,
        llm_client=MagicMock(spec=LiteLLMClient),
        policy_manager=mock_policy_manager,
        emitter=NullEventEmitter(),
        api_key="test-api-key",
        admin_key="test-admin-key",
        anthropic_client=mock_anthropic_client,
    )

    app = FastAPI()
    app.include_router(router)

    # Set up dependencies directly on app state
    app.state.dependencies = deps

    return app


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


class TestAnthropicMessagesEndpoint:
    """Tests for the /v1/messages endpoint."""

    def test_non_streaming_request_returns_anthropic_response(self, client, mock_anthropic_client):
        """Test that non-streaming request returns Anthropic-formatted response."""
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test-api-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
                "stream": False,
            },
        )

        assert response.status_code == 200
        data = response.json()

        # Verify Anthropic response format
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert "content" in data
        assert data["model"] == DEFAULT_CLAUDE_TEST_MODEL
        assert data["stop_reason"] == "end_turn"
        assert "usage" in data

        # Verify the mock was called
        mock_anthropic_client.complete.assert_called_once()

    def test_missing_api_key_returns_401(self, client):
        """Test that missing API key returns 401."""
        response = client.post(
            "/v1/messages",
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
            },
        )

        assert response.status_code == 401

    def test_invalid_api_key_returns_401(self, client):
        """Test that invalid API key returns 401."""
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer wrong-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
            },
        )

        assert response.status_code == 401

    def test_missing_model_returns_400(self, client):
        """Test that missing model field returns 400."""
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test-api-key"},
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
            },
        )

        assert response.status_code == 400
        assert "model" in response.json()["detail"].lower()

    def test_missing_messages_returns_400(self, client):
        """Test that missing messages field returns 400."""
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test-api-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "max_tokens": 1024,
            },
        )

        assert response.status_code == 400
        assert "messages" in response.json()["detail"].lower()

    def test_missing_max_tokens_returns_400(self, client):
        """Test that missing max_tokens field returns 400."""
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test-api-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

        assert response.status_code == 400
        assert "max_tokens" in response.json()["detail"].lower()

    def test_x_call_id_header_in_response(self, client, mock_anthropic_client):
        """Test that X-Call-ID header is present in response."""
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test-api-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
                "stream": False,
            },
        )

        assert response.status_code == 200
        assert "x-call-id" in response.headers

    def test_supports_x_api_key_header(self, client, mock_anthropic_client):
        """Test that x-api-key header is also accepted for auth."""
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "test-api-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
                "stream": False,
            },
        )

        assert response.status_code == 200


class TestAnthropicStreaming:
    """Tests for streaming responses."""

    @pytest.fixture
    def mock_streaming_client(self):
        """Create a mock AnthropicClient that returns streaming events."""
        client = MagicMock(spec=AnthropicClient)
        client._base_url = None

        async def mock_stream(request):
            # Simulate streaming events as Pydantic models
            events = [
                MagicMock(
                    type="message_start",
                    model_dump=lambda: {
                        "type": "message_start",
                        "message": {
                            "id": "msg_123",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": DEFAULT_CLAUDE_TEST_MODEL,
                            "stop_reason": None,
                            "usage": {"input_tokens": 10, "output_tokens": 0},
                        },
                    },
                ),
                MagicMock(
                    type="content_block_start",
                    model_dump=lambda: {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                MagicMock(
                    type="content_block_delta",
                    model_dump=lambda: {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hello!"},
                    },
                ),
                MagicMock(
                    type="content_block_stop",
                    model_dump=lambda: {"type": "content_block_stop", "index": 0},
                ),
                MagicMock(
                    type="message_delta",
                    model_dump=lambda: {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 2},
                    },
                ),
                MagicMock(
                    type="message_stop",
                    model_dump=lambda: {"type": "message_stop"},
                ),
            ]
            for event in events:
                yield event

        client.stream = mock_stream
        return client

    @pytest.fixture
    def streaming_test_app(self, mock_policy_manager, mock_streaming_client, mock_redis):
        """Create a test app with streaming mock."""
        from fastapi import FastAPI

        from luthien_proxy.gateway_routes import router

        deps = Dependencies(
            db_pool=None,
            redis_client=mock_redis,
            llm_client=MagicMock(spec=LiteLLMClient),
            policy_manager=mock_policy_manager,
            emitter=NullEventEmitter(),
            api_key="test-api-key",
            admin_key="test-admin-key",
            anthropic_client=mock_streaming_client,
        )

        app = FastAPI()
        app.include_router(router)
        app.state.dependencies = deps

        return app

    def test_streaming_returns_sse_format(self, streaming_test_app, mock_streaming_client):
        """Test that streaming request returns SSE formatted events."""
        client = TestClient(streaming_test_app)

        with client.stream(
            "POST",
            "/v1/messages",
            headers={"Authorization": "Bearer test-api-key"},
            json={
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1024,
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

            # Collect all events
            events = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    events.append(line)

        # Verify we got the expected event types
        assert any("message_start" in e for e in events)
        assert any("content_block_delta" in e for e in events)
        assert any("message_stop" in e for e in events)
