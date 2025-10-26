# ABOUTME: Test fixtures and helpers for gateway_routes tests
# ABOUTME: Provides mock factories and app setup to avoid external I/O

"""Test fixtures for gateway routes testing."""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.v2.gateway_routes import router


@pytest.fixture
def mock_control_plane():
    """Create a mock control plane that passes requests/responses through unchanged."""
    mock_cp = Mock()

    async def process_request(request, _call_id):
        return request

    async def process_full_response(response, _call_id):
        return response

    async def process_streaming_response(stream, _call_id, **_kwargs):
        async for chunk in stream:
            yield chunk

    mock_cp.process_request = process_request
    mock_cp.process_full_response = process_full_response
    mock_cp.process_streaming_response = process_streaming_response

    return mock_cp


@pytest.fixture
def app(mock_control_plane):
    """Create a test FastAPI app with gateway routes.

    Sets app.state dependencies to None to ensure no external I/O occurs:
    - db_pool=None: emit_request_event/emit_response_event return early
    - event_publisher=None: publish_event calls are skipped
    - redis_client=None: no Redis operations occur
    """
    app = FastAPI()
    app.include_router(router)

    app.state.api_key = "test-api-key"
    app.state.control_plane = mock_control_plane
    app.state.db_pool = None
    app.state.event_publisher = None
    app.state.redis_client = None

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


def make_mock_response(model="gpt-4", content="Hello", finish_reason="stop"):
    """Helper to create a mock LiteLLM response with all required attributes."""
    mock_response = Mock()
    mock_response.model_dump.return_value = {
        "id": "resp-123",
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    }
    # Add attributes for format converters (Anthropic endpoint)
    mock_response.id = "resp-123"
    mock_response.model = model
    mock_choice = Mock()
    mock_message = Mock()
    mock_message.content = content
    mock_message.tool_calls = None  # Explicitly set to None to avoid Mock iteration issues
    mock_choice.message = mock_message
    mock_choice.finish_reason = finish_reason
    mock_response.choices = [mock_choice]
    mock_usage = Mock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 20
    mock_response.usage = mock_usage
    return mock_response


def make_mock_stream_chunk(content="chunk", index=0):
    """Helper to create a mock streaming chunk."""
    chunk = Mock()
    chunk.model_dump_json.return_value = f'{{"content":"{content}","index":{index}}}'
    return chunk


async def mock_acompletion_non_streaming(model="gpt-4", content="Hello"):
    """Create a mock acompletion for non-streaming responses."""
    return make_mock_response(model=model, content=content)


def mock_acompletion_streaming(*chunks):
    """Create a mock acompletion for streaming responses.

    Args:
        *chunks: Variable number of (content, index) tuples

    Returns:
        Async function that returns an async generator
    """

    async def _mock(**kwargs):
        async def chunk_generator():
            for content, index in chunks:
                yield make_mock_stream_chunk(content, index)

        return chunk_generator()

    return _mock
