"""Mock e2e tests verifying the gateway forwards request parameters to the backend.

Checks that fields like temperature, top_p, stop_sequences, system, max_tokens,
model, messages, and metadata reach the mock Anthropic server unchanged.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_request_forwarding.py -v
"""

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e


async def _post(client: httpx.AsyncClient, gateway_url: str, auth_headers: dict, extra_fields: dict) -> httpx.Response:
    body = {
        "model": "claude-haiku-4-5",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 100,
        "stream": False,
        **extra_fields,
    }
    return await client.post(f"{gateway_url}/v1/messages", json=body, headers=auth_headers)


@pytest.mark.asyncio
async def test_temperature_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards the temperature parameter to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"temperature": 0.7})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["temperature"] == 0.7


@pytest.mark.asyncio
async def test_top_p_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards the top_p parameter to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"top_p": 0.9})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["top_p"] == 0.9


@pytest.mark.asyncio
async def test_max_tokens_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards the max_tokens parameter to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"max_tokens": 42})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["max_tokens"] == 42


@pytest.mark.asyncio
async def test_stop_sequences_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards stop_sequences to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"stop_sequences": ["STOP", "END"]})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["stop_sequences"] == ["STOP", "END"]


@pytest.mark.asyncio
async def test_system_prompt_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards the system prompt to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"system": "You are a helpful assistant"})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["system"] == "You are a helpful assistant"


@pytest.mark.asyncio
async def test_model_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards the model field to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"model": "claude-haiku-4-5"})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_multiple_messages_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards all messages in a multi-turn conversation unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    messages = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "msg2"},
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        body = {
            "model": "claude-haiku-4-5",
            "messages": messages,
            "max_tokens": 100,
            "stream": False,
        }
        response = await client.post(f"{gateway_url}/v1/messages", json=body, headers=auth_headers)

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert len(last["messages"]) == 3
    assert last["messages"][-1]["content"] == "msg2"


@pytest.mark.asyncio
async def test_metadata_forwarded(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway forwards the metadata field to the backend unchanged."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"metadata": {"user_id": "test_user_123"}})

    assert response.status_code == 200
    last = mock_anthropic.last_request()
    assert last is not None
    assert last["metadata"]["user_id"] == "test_user_123"


@pytest.mark.asyncio
async def test_request_capture_accumulates_across_requests(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Mock server accumulates all request bodies across multiple requests."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))
    mock_anthropic.enqueue(text_response("ok"))
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        for _ in range(3):
            await _post(client, gateway_url, auth_headers, {})

    assert len(mock_anthropic.received_requests()) == 3


@pytest.mark.asyncio
async def test_unknown_extra_field_not_forwarded_or_ignored(
    mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url: str, auth_headers: dict
):
    """Gateway does not crash when the client sends an unrecognised field."""
    mock_anthropic.clear_requests()
    mock_anthropic.enqueue(text_response("ok"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _post(client, gateway_url, auth_headers, {"x_custom_field": "custom_value"})

    assert response.status_code == 200
