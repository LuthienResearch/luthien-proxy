"""E2E tests for extra parameter pass-through.

These tests verify that extra model parameters (like `thinking`, `metadata`,
`stop_sequences`, etc.) are correctly passed through the gateway to the backend LLM.

The gateway converts Anthropic requests to an internal OpenAI format, and these
tests ensure that extra parameters aren't dropped during conversion.
"""

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL

# === Test Configuration ===


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


# === Anthropic Client Extra Parameters ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_metadata_parameter_accepted(http_client, gateway_healthy):
    """Verify Anthropic endpoint accepts and passes through metadata parameter."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
            "metadata": {"user_id": "test-user-123", "custom_field": "custom_value"},
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Verify response is valid Anthropic format
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_stop_sequences_parameter(http_client, gateway_healthy):
    """Verify stop_sequences parameter is passed through and affects response."""
    # Request model to count, but stop at "3"
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Count from 1 to 10, one number per line."}],
            "max_tokens": 100,
            "stream": False,
            "stop_sequences": ["5"],
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Verify response is valid
    assert data["type"] == "message"
    assert len(data["content"]) > 0

    # The response should have stopped before or at "5"
    response_text = data["content"][0]["text"]
    # If stop_sequences worked, "6", "7", "8", "9", "10" should not appear
    # (allowing for some model variation in output format)
    assert "10" not in response_text, f"stop_sequences didn't work - response contains '10': {response_text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_tool_choice_parameter(http_client, gateway_healthy):
    """Verify tool_choice parameter is accepted with tools."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
            "max_tokens": 100,
            "stream": False,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                        },
                        "required": ["location"],
                    },
                }
            ],
            "tool_choice": {"type": "auto"},
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Verify response is valid Anthropic format
    assert data["type"] == "message"
    assert data["role"] == "assistant"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_multiple_extra_params(http_client, gateway_healthy):
    """Verify multiple extra parameters can be passed together."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say 'hello world'"}],
            "max_tokens": 50,
            "stream": False,
            "metadata": {"user_id": "multi-param-test"},
            "stop_sequences": ["goodbye"],
            "temperature": 0.5,
            "top_p": 0.9,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["type"] == "message"
    assert len(data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_custom_unknown_param(http_client, gateway_healthy):
    """Verify unknown/custom parameters don't cause errors."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
            "custom_tracking_id": "e2e-test-12345",
            "internal_flag": True,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    # Request should succeed even with unknown params
    # (LiteLLM may drop them, but gateway shouldn't error)
    assert response.status_code == 200, f"Request failed: {response.text}"


# === OpenAI Client Extra Parameters ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_stop_parameter(http_client, gateway_healthy):
    """Verify OpenAI stop parameter is passed through."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Count from 1 to 10, one number per line."}],
            "max_tokens": 100,
            "stream": False,
            "stop": ["5"],
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0

    response_text = data["choices"][0]["message"]["content"]
    assert "10" not in response_text, f"stop parameter didn't work - response contains '10': {response_text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_seed_parameter(http_client, gateway_healthy):
    """Verify seed parameter is accepted for reproducible outputs."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Pick a random number between 1 and 100"}],
            "max_tokens": 20,
            "stream": False,
            "seed": 42,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_response_format_parameter(http_client, gateway_healthy):
    """Verify response_format parameter is accepted."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Return a JSON object with a 'greeting' field"}],
            "max_tokens": 50,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["object"] == "chat.completion"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_logprobs_parameter(http_client, gateway_healthy):
    """Verify logprobs parameter is accepted."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
            "logprobs": True,
            "top_logprobs": 3,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["object"] == "chat.completion"
    # logprobs should be in the response if the model supports it
    # (not all models do, so we just verify the request succeeds)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_presence_frequency_penalty(http_client, gateway_healthy):
    """Verify presence_penalty and frequency_penalty are accepted."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Write a short poem about coding"}],
            "max_tokens": 100,
            "stream": False,
            "presence_penalty": 0.5,
            "frequency_penalty": 0.5,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_user_parameter(http_client, gateway_healthy):
    """Verify user parameter for tracking is accepted."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": False,
            "user": "e2e-test-user-12345",
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    assert data["object"] == "chat.completion"


# === Streaming with Extra Parameters ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_streaming_with_extra_params(http_client, gateway_healthy):
    """Verify extra parameters work with streaming Anthropic requests."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": True,
            "metadata": {"user_id": "streaming-test"},
            "stop_sequences": ["goodbye"],
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        event_lines = []
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event_lines.append(line)

        assert len(event_lines) > 0, "Should receive SSE events"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_streaming_with_extra_params(http_client, gateway_healthy):
    """Verify extra parameters work with streaming OpenAI requests."""
    async with http_client.stream(
        "POST",
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 20,
            "stream": True,
            "stop": ["goodbye"],
            "seed": 42,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        data_lines = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data_lines.append(line)

        assert len(data_lines) > 0, "Should receive SSE data chunks"


# === Cross-format Extra Parameters ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_to_openai_backend_with_extra_params(http_client, gateway_healthy):
    """Verify extra params work when Anthropic client talks to OpenAI backend."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "gpt-3.5-turbo",  # OpenAI model via Anthropic endpoint
            "messages": [{"role": "user", "content": "Say hello briefly"}],
            "max_tokens": 20,
            "stream": False,
            "metadata": {"test": "cross-format"},
            "stop_sequences": ["goodbye"],
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Should get Anthropic format response
    assert data["type"] == "message"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_to_anthropic_backend_with_extra_params(http_client, gateway_healthy):
    """Verify extra params work when OpenAI client talks to Anthropic backend."""
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "claude-haiku-4-5",  # Anthropic model via OpenAI endpoint
            "messages": [{"role": "user", "content": "Say hello briefly"}],
            "max_tokens": 20,
            "stream": False,
            "stop": ["goodbye"],
            "seed": 42,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.text}"
    data = response.json()

    # Should get OpenAI format response
    assert data["object"] == "chat.completion"
