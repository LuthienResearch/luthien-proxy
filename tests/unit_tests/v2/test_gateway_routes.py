# ABOUTME: Unit tests for V2 gateway routes and helper functions
# ABOUTME: Tests authentication, hashing, streaming utilities for OpenAI and Anthropic endpoints

"""Tests for V2 gateway routes."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from tests.unit_tests.v2.gateway_test_fixtures import (
    app,  # noqa: F401 - pytest fixture
    client,  # noqa: F401 - pytest fixture
    make_mock_response,
    mock_acompletion_streaming,
    mock_control_plane,  # noqa: F401 - pytest fixture
)

from luthien_proxy.v2.gateway_routes import (
    add_model_specific_params,
    hash_api_key,
    publish_request_received_event,
    publish_request_sent_event,
    stream_llm_chunks,
    verify_token,
)


class TestAuthentication:
    """Test authentication helpers."""

    def test_hash_api_key(self):
        """Test API key hashing produces consistent, deterministic hashes."""
        key = "my-secret-key"
        hashed = hash_api_key(key)
        assert isinstance(hashed, str) and len(hashed) == 16
        assert hash_api_key(key) == hashed
        assert hash_api_key("different-key") != hashed

    @pytest.mark.parametrize("key", ["abc", "this-is-a-medium-key", "x" * 100])
    def test_hash_api_key_lengths(self, key):
        """Test hashing works with various key lengths."""
        assert len(hash_api_key(key)) == 16

    @pytest.mark.parametrize(
        "credentials,should_pass",
        [
            (HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-key-123"), True),
            (HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-key"), False),
            (None, False),  # No credentials at all
        ],
    )
    def test_verify_token_with_bearer(self, credentials, should_pass):
        """Test token verification with Authorization: Bearer header."""
        mock_request = Mock()
        mock_request.app.state.api_key = "valid-key-123"
        mock_request.headers.get.return_value = None  # No x-api-key header

        if should_pass:
            assert verify_token(mock_request, credentials) == "valid-key-123"
        else:
            with pytest.raises(HTTPException) as exc_info:
                verify_token(mock_request, credentials)
            assert exc_info.value.status_code == 401

    @pytest.mark.parametrize(
        "x_api_key,should_pass",
        [
            ("valid-key-123", True),
            ("wrong-key", False),
            (None, False),
        ],
    )
    def test_verify_token_with_x_api_key(self, x_api_key, should_pass):
        """Test token verification with x-api-key header (Anthropic-style)."""
        mock_request = Mock()
        mock_request.app.state.api_key = "valid-key-123"
        mock_request.headers.get.return_value = x_api_key

        # No Bearer credentials
        credentials = None

        if should_pass:
            assert verify_token(mock_request, credentials) == "valid-key-123"
        else:
            with pytest.raises(HTTPException) as exc_info:
                verify_token(mock_request, credentials)
            assert exc_info.value.status_code == 401


class TestHelperFunctions:
    """Test helper functions."""

    @pytest.mark.parametrize(
        "data,known_params,expected",
        [
            ({"model": "gpt-4", "verbosity": "debug", "temperature": 0.7}, {"verbosity"}, ["verbosity"]),
            ({"model": "gpt-4", "temperature": 0.7}, {"verbosity"}, None),
            (
                {"model": "gpt-4", "verbosity": "debug", "custom": "value"},
                {"verbosity", "custom"},
                ["verbosity", "custom"],
            ),
        ],
    )
    def test_add_model_specific_params(self, data, known_params, expected):
        """Test adding model-specific params."""
        result = add_model_specific_params(data.copy(), known_params)
        if expected:
            assert set(result["allowed_openai_params"]) == set(expected)
        else:
            assert "allowed_openai_params" not in result

    @pytest.mark.parametrize(
        "func,params",
        [
            (publish_request_received_event, {"endpoint": "/v1/chat/completions", "model": "gpt-4", "stream": False}),
            (publish_request_sent_event, {"model": "gpt-4", "stream": False}),
        ],
    )
    @pytest.mark.asyncio
    async def test_publish_events_none_publisher(self, func, params):
        """Test publishing when event_publisher is None doesn't raise errors."""
        await func(event_publisher=None, call_id="test-123", **params)

    @pytest.mark.parametrize(
        "func,event_type,params",
        [
            (
                publish_request_received_event,
                "gateway.request_received",
                {"endpoint": "/v1/chat/completions", "model": "gpt-4", "stream": False},
            ),
            (publish_request_sent_event, "gateway.request_sent", {"model": "gpt-4", "stream": False}),
        ],
    )
    @pytest.mark.asyncio
    async def test_publish_events(self, func, event_type, params):
        """Test publishing request events."""
        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock()
        await func(event_publisher=mock_publisher, call_id="test-123", **params)

        call_args = mock_publisher.publish_event.call_args
        assert call_args[1]["call_id"] == "test-123"
        assert call_args[1]["event_type"] == event_type
        for key, value in params.items():
            assert call_args[1]["data"][key] == value


class TestStreamingHelpers:
    """Test streaming helper functions."""

    @pytest.mark.asyncio
    async def test_stream_llm_chunks(self):
        """Test streaming chunks from LiteLLM."""
        mock_chunks = [Mock(), Mock(), Mock()]

        async def mock_acompletion(**kwargs):
            async def chunk_generator():
                for chunk in mock_chunks:
                    yield chunk

            return chunk_generator()

        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", mock_acompletion):
            chunks = [chunk async for chunk in stream_llm_chunks({"model": "gpt-4"})]
            assert chunks == mock_chunks


class TestStreamWithPolicyControl:
    """Test stream_with_policy_control function."""

    @pytest.mark.asyncio
    async def test_dict_chunks(self):
        """Test handling of dict chunks in policy stream."""
        from luthien_proxy.v2.gateway_routes import stream_with_policy_control

        mock_cp = Mock()

        async def mock_policy_stream(_stream, _call_id, **_kwargs):
            yield {"type": "chunk", "text": "hello"}
            yield {"type": "chunk", "text": "world"}

        mock_cp.process_streaming_response = mock_policy_stream

        chunks = [
            chunk
            async for chunk in stream_with_policy_control(
                data={"model": "gpt-4"},
                call_id="test-call",
                control_plane=mock_cp,
                db_pool=None,
                redis_client=None,
            )
        ]

        assert len(chunks) == 2
        assert 'data: {"type": "chunk", "text": "hello"}' in chunks[0]

    @pytest.mark.asyncio
    async def test_unknown_chunk_type(self):
        """Test handling of unknown chunk types."""
        from luthien_proxy.v2.gateway_routes import stream_with_policy_control

        class UnknownChunk:
            pass

        mock_cp = Mock()

        async def mock_policy_stream(_stream, _call_id, **_kwargs):
            yield UnknownChunk()

        mock_cp.process_streaming_response = mock_policy_stream

        chunks = [
            chunk
            async for chunk in stream_with_policy_control(
                data={"model": "gpt-4"},
                call_id="test-call",
                control_plane=mock_cp,
                db_pool=None,
                redis_client=None,
            )
        ]

        assert len(chunks) == 1
        assert "error" in chunks[0].lower() and "unknown chunk type" in chunks[0].lower()

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test error handling in streaming."""
        from luthien_proxy.v2.gateway_routes import stream_with_policy_control

        mock_cp = Mock()

        async def mock_policy_stream(_stream, _call_id, **_kwargs):
            yield {"type": "chunk", "text": "first"}
            raise ValueError("Test streaming error")

        mock_cp.process_streaming_response = mock_policy_stream

        chunks = [
            chunk
            async for chunk in stream_with_policy_control(
                data={"model": "gpt-4"},
                call_id="test-call",
                control_plane=mock_cp,
                db_pool=None,
                redis_client=None,
            )
        ]

        assert len(chunks) >= 1 and "error" in chunks[-1].lower()


class TestProcessNonStreamingResponse:
    """Test process_non_streaming_response function."""

    @pytest.mark.asyncio
    async def test_event_publisher_called(self):
        """Test that event publisher is called for both received and sent events."""
        from luthien_proxy.v2.gateway_routes import process_non_streaming_response

        mock_response = make_mock_response()
        mock_cp = Mock()

        async def process_full_response(response, _call_id):
            return response

        mock_cp.process_full_response = process_full_response

        mock_publisher = Mock()
        mock_publisher.publish_event = AsyncMock()

        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", AsyncMock(return_value=mock_response)):
            await process_non_streaming_response(
                data={"model": "gpt-4", "stream": False},
                call_id="test-call",
                control_plane=mock_cp,
                db_pool=None,
                event_publisher=mock_publisher,
            )

        assert mock_publisher.publish_event.call_count == 2
        calls = mock_publisher.publish_event.call_args_list
        assert calls[0][1]["event_type"] == "gateway.response_received"
        assert calls[1][1]["event_type"] == "gateway.response_sent"


class TestOpenAIEndpoint:
    """Test OpenAI chat completions endpoint."""

    def test_non_streaming(self, client):  # noqa: F811
        """Test non-streaming request."""
        mock_response = make_mock_response()

        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", AsyncMock(return_value=mock_response)):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
                headers={"Authorization": "Bearer test-api-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "gpt-4" and len(data["choices"]) == 1

    def test_streaming(self, client):  # noqa: F811
        """Test streaming request."""
        with patch(
            "luthien_proxy.v2.gateway_routes.litellm.acompletion",
            mock_acompletion_streaming(("hello", 0), ("world", 1)),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}], "stream": True},
                headers={"Authorization": "Bearer test-api-key"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_unauthorized(self, client):  # noqa: F811
        """Test invalid API key."""
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_auth_with_x_api_key(self, client):  # noqa: F811
        """Test authentication with x-api-key header (Anthropic-style)."""
        mock_response = make_mock_response()

        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", AsyncMock(return_value=mock_response)):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
                headers={"x-api-key": "test-api-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "gpt-4"

    def test_auth_x_api_key_invalid(self, client):  # noqa: F811
        """Test invalid x-api-key header."""
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
            headers={"x-api-key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_with_trace_id(self, client):  # noqa: F811
        """Test request with trace_id in metadata."""
        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", AsyncMock(return_value=make_mock_response())):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "test"}],
                    "metadata": {"trace_id": "trace-123"},
                },
                headers={"Authorization": "Bearer test-api-key"},
            )
        assert response.status_code == 200

    def test_error_handling(self, client):  # noqa: F811
        """Test error handling."""
        with patch(
            "luthien_proxy.v2.gateway_routes.litellm.acompletion", AsyncMock(side_effect=ValueError("LLM error"))
        ):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
                headers={"Authorization": "Bearer test-api-key"},
            )

        assert response.status_code == 500
        assert "LLM error" in response.json()["detail"]


class TestAnthropicEndpoint:
    """Test Anthropic messages endpoint."""

    def test_non_streaming(self, client):  # noqa: F811
        """Test non-streaming request."""
        with patch(
            "luthien_proxy.v2.gateway_routes.litellm.acompletion",
            AsyncMock(return_value=make_mock_response(model="claude-3")),
        ):
            response = client.post(
                "/v1/messages",
                json={"model": "claude-3", "messages": [{"role": "user", "content": "test"}]},
                headers={"Authorization": "Bearer test-api-key"},
            )
        assert response.status_code == 200

    def test_streaming(self, client):  # noqa: F811
        """Test streaming request."""
        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", mock_acompletion_streaming(("hello", 0))):
            response = client.post(
                "/v1/messages",
                json={"model": "claude-3", "messages": [{"role": "user", "content": "test"}], "stream": True},
                headers={"Authorization": "Bearer test-api-key"},
            )
        assert response.status_code == 200

    def test_auth_with_x_api_key(self, client):  # noqa: F811
        """Test authentication with x-api-key header (native Anthropic format)."""
        with patch(
            "luthien_proxy.v2.gateway_routes.litellm.acompletion",
            AsyncMock(return_value=make_mock_response(model="claude-3")),
        ):
            response = client.post(
                "/v1/messages",
                json={"model": "claude-3", "messages": [{"role": "user", "content": "test"}]},
                headers={"x-api-key": "test-api-key"},
            )
        assert response.status_code == 200

    def test_auth_x_api_key_invalid(self, client):  # noqa: F811
        """Test invalid x-api-key header."""
        response = client.post(
            "/v1/messages",
            json={"model": "claude-3", "messages": [{"role": "user", "content": "test"}]},
            headers={"x-api-key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_error_handling(self, client):  # noqa: F811
        """Test error handling."""
        with patch(
            "luthien_proxy.v2.gateway_routes.litellm.acompletion", AsyncMock(side_effect=ValueError("LLM error"))
        ):
            response = client.post(
                "/v1/messages",
                json={"model": "claude-3", "messages": [{"role": "user", "content": "test"}]},
                headers={"Authorization": "Bearer test-api-key"},
            )

        assert response.status_code == 500
        assert "LLM error" in response.json()["detail"]
