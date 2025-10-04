"""ABOUTME: Common request patterns for E2E tests.

ABOUTME: Provides utilities for making standardized API requests and consuming streams.
"""

import json
from typing import Any

import httpx


async def make_streaming_request(
    model: str = "dummy-agent",
    content: str = "test",
    api_key: str = "sk-luthien-dev-key",
    base_url: str = "http://localhost:4000",
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    """Make a streaming chat completion request and consume the stream.

    Args:
        model: Model to use (default: "dummy-agent")
        content: Message content (default: "test")
        api_key: API key for authorization (default: "sk-luthien-dev-key")
        base_url: Base URL for the API (default: "http://localhost:4000")

    Returns:
        Tuple of (response, chunks) where chunks is a list of parsed SSE chunks
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": True,
            },
        )

        chunks = []
        async for line in response.aiter_lines():
            if line.startswith("data: ") and not line.endswith("[DONE]"):
                chunk = json.loads(line[6:])
                chunks.append(chunk)

        return response, chunks


async def consume_streaming_response(response: httpx.Response) -> list[dict[str, Any]]:
    """Consume a streaming response and return parsed chunks.

    Args:
        response: HTTP response with streaming SSE content

    Returns:
        List of parsed SSE chunks (excluding [DONE] sentinel)
    """
    chunks = []
    async for line in response.aiter_lines():
        if line.startswith("data: ") and not line.endswith("[DONE]"):
            chunk = json.loads(line[6:])
            chunks.append(chunk)
    return chunks


async def make_nonstreaming_request(
    model: str = "dummy-agent",
    content: str = "test",
    api_key: str = "sk-luthien-dev-key",
    base_url: str = "http://localhost:4000",
) -> tuple[httpx.Response, dict[str, Any]]:
    """Make a non-streaming chat completion request.

    Args:
        model: Model to use (default: "dummy-agent")
        content: Message content (default: "test")
        api_key: API key for authorization (default: "sk-luthien-dev-key")
        base_url: Base URL for the API (default: "http://localhost:4000")

    Returns:
        Tuple of (response, data) where data is the parsed JSON response
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
            },
        )

        data = response.json()
        return response, data
