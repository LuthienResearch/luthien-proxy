"""ABOUTME: E2E tests for V2 architecture API compatibility.
ABOUTME: Tests OpenAI and Anthropic client APIs against different backend models with streaming/non-streaming.

Run with: uv run pytest tests/e2e_tests/test_v2_api_compatibility.py -v -m e2e

Self-contained tests that start their own V2 gateway instance.
Requires OPENAI_API_KEY and ANTHROPIC_API_KEY environment variables set.
"""

from __future__ import annotations

import json

import httpx
import pytest
from tests.e2e_tests.helpers import V2GatewayManager

pytestmark = pytest.mark.e2e


# ==============================================================================
# Test Parameters
# ==============================================================================

# Client API formats (what the client sends)
CLIENT_APIS = [
    "openai",  # OpenAI chat completions format
    "anthropic",  # Anthropic messages format
]

# Backend models to test against
# See https://platform.openai.com/docs/models/gpt-5-nano
# and https://www.anthropic.com/news/claude-haiku-4-5
BACKEND_MODELS = [
    "gpt-5-nano",  # OpenAI GPT-5 nano
    "claude-haiku-4-5",  # Anthropic Haiku 4.5
]

# Streaming modes
STREAMING_MODES = [
    False,  # Non-streaming
    True,  # Streaming
]


# ==============================================================================
# Helper Functions
# ==============================================================================


async def make_openai_request(
    model: str,
    content: str,
    stream: bool,
    base_url: str,
    api_key: str,
) -> tuple[httpx.Response, dict | list]:
    """Make an OpenAI-format request (Responses API for gpt-5, Chat API for others).

    Args:
        model: Model to use
        content: Message content
        stream: Whether to stream the response
        base_url: Base URL for the API
        api_key: API key for authorization

    Returns:
        Tuple of (response, data) where data is either dict (non-streaming) or list of chunks (streaming)
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Build request payload
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": stream,
        }

        # gpt-5 models use verbosity parameter for controlling reasoning output
        if "gpt-5" in model:
            payload["verbosity"] = "low"

        response = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )

        if stream:
            # Parse streaming response
            chunks = []
            async for line in response.aiter_lines():
                if line.startswith("data: ") and not line.endswith("[DONE]"):
                    chunk = json.loads(line[6:])
                    chunks.append(chunk)
            return response, chunks
        else:
            # Parse non-streaming response
            data = response.json()
            return response, data


async def make_anthropic_request(
    model: str,
    content: str,
    stream: bool,
    base_url: str,
    api_key: str,
) -> tuple[httpx.Response, dict | list]:
    """Make an Anthropic-format messages request.

    Args:
        model: Model to use
        content: Message content
        stream: Whether to stream the response
        base_url: Base URL for the API
        api_key: API key for authorization

    Returns:
        Tuple of (response, data) where data is either dict (non-streaming) or list of events (streaming)
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/v1/messages",
            headers={
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": stream,
                "max_tokens": 1024,  # Anthropic requires max_tokens
            },
        )

        if stream:
            # Parse streaming response (SSE format)
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    events.append(event)
            return response, events
        else:
            # Parse non-streaming response
            data = response.json()
            return response, data


def extract_openai_content(data: dict | list, stream: bool) -> str:
    """Extract content from OpenAI response.

    Args:
        data: Response data (dict for non-streaming, list of chunks for streaming)
        stream: Whether this is a streaming response

    Returns:
        Extracted content as string
    """
    if stream:
        # Combine streaming chunks
        content_parts = []
        for chunk in data:
            if "choices" in chunk and chunk["choices"]:
                delta = chunk["choices"][0].get("delta", {})
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
        return "".join(content_parts)
    else:
        # Extract from non-streaming response
        if "choices" in data and data["choices"]:
            message = data["choices"][0].get("message", {})
            return message.get("content", "")
        return ""


def extract_anthropic_content(data: dict | list, stream: bool) -> str:
    """Extract content from Anthropic response.

    Args:
        data: Response data (dict for non-streaming, list of events for streaming)
        stream: Whether this is a streaming response

    Returns:
        Extracted content as string
    """
    if stream:
        # Combine streaming events
        content_parts = []
        for event in data:
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    content_parts.append(delta.get("text", ""))
        return "".join(content_parts)
    else:
        # Extract from non-streaming response
        if "content" in data and data["content"]:
            for block in data["content"]:
                if block.get("type") == "text":
                    return block.get("text", "")
        return ""


# ==============================================================================
# Parameterized Tests
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("client_api", CLIENT_APIS)
@pytest.mark.parametrize("backend_model", BACKEND_MODELS)
@pytest.mark.parametrize("stream", STREAMING_MODES)
async def test_v2_api_compatibility(
    client_api: str,
    backend_model: str,
    stream: bool,
    v2_gateway: V2GatewayManager,
) -> None:
    """Test V2 architecture with all permutations of client API, backend model, and streaming mode.

    This test validates that:
    1. The V2 gateway correctly handles both OpenAI and Anthropic client APIs
    2. Both backend models (OpenAI gpt-5-nano and Anthropic claude-haiku-4-5) work correctly
    3. Both streaming and non-streaming modes work
    4. The NoOp policy passes everything through unchanged
    """
    # Test message
    test_content = "Say 'hello' in exactly one word."

    # Make request based on client API
    if client_api == "openai":
        response, data = await make_openai_request(
            model=backend_model,
            content=test_content,
            stream=stream,
            base_url=v2_gateway.base_url,
            api_key=v2_gateway.api_key,
        )
        content = extract_openai_content(data, stream)
    else:  # anthropic
        response, data = await make_anthropic_request(
            model=backend_model,
            content=test_content,
            stream=stream,
            base_url=v2_gateway.base_url,
            api_key=v2_gateway.api_key,
        )
        content = extract_anthropic_content(data, stream)

    # Assertions
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert content, f"Expected non-empty content, got: {content!r}"

    # Verify we got a reasonable response (should contain "hello" or similar greeting)
    content_lower = content.lower()
    assert any(word in content_lower for word in ["hello", "hi", "hey", "greetings"]), (
        f"Expected greeting in response, got: {content!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("client_api", CLIENT_APIS)
@pytest.mark.parametrize("backend_model", BACKEND_MODELS)
async def test_v2_error_handling(
    client_api: str,
    backend_model: str,
    v2_gateway: V2GatewayManager,
) -> None:
    """Test that V2 architecture handles errors correctly.

    Tests error scenarios like:
    - Invalid requests
    - Missing required fields
    """
    # Test with missing messages field
    async with httpx.AsyncClient(timeout=30.0) as client:
        if client_api == "openai":
            response = await client.post(
                f"{v2_gateway.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {v2_gateway.api_key}"},
                json={
                    "model": backend_model,
                    # Missing "messages" field
                    "stream": False,
                },
            )
        else:  # anthropic
            response = await client.post(
                f"{v2_gateway.base_url}/v1/messages",
                headers={
                    "Authorization": f"Bearer {v2_gateway.api_key}",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": backend_model,
                    # Missing "messages" field
                    "stream": False,
                    "max_tokens": 100,
                },
            )

    # Should return an error status code
    assert response.status_code >= 400, f"Expected error status code, got {response.status_code}"
