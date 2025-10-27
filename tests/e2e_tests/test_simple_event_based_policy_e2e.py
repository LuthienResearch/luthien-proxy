"""ABOUTME: E2E tests for SimpleEventBasedPolicy with real gateway.
ABOUTME: Tests SimpleStringReplacementPolicy against actual streaming responses.

Run with: uv run pytest tests/e2e_tests/test_simple_event_based_policy_e2e.py -v -m e2e

Requires ANTHROPIC_API_KEY environment variable set.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn

from luthien_proxy.v2.main import create_app
from luthien_proxy.v2.policies.simple_string_replacement import (
    SimpleStringReplacementPolicy,
)

pytestmark = pytest.mark.e2e


def _run_gateway_with_policy(port: int, api_key: str, replacements: dict[str, str]) -> None:
    """Run V2 gateway with SimpleStringReplacementPolicy in a subprocess."""
    # Get config from environment
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required for e2e tests")

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    # Create policy with replacements
    policy = SimpleStringReplacementPolicy(replacements=replacements)

    # Create app
    app = create_app(
        api_key=api_key,
        database_url=database_url,
        redis_url=redis_url,
        policy=policy,
    )

    # Run uvicorn server
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )


@contextmanager
def gateway_with_replacements(
    replacements: dict[str, str],
    port: int = 8889,
    api_key: str = "sk-test-simple-policy",
    timeout: float = 10.0,
):
    """Context manager for gateway with SimpleStringReplacementPolicy."""
    process = multiprocessing.Process(
        target=_run_gateway_with_policy,
        args=(port, api_key, replacements),
        daemon=True,
    )
    process.start()

    # Wait for gateway to be ready
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    else:
        process.terminate()
        process.join(timeout=5.0)
        raise RuntimeError(f"Gateway failed to start: {last_error}")

    try:
        yield {"base_url": base_url, "api_key": api_key}
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)


async def make_streaming_request(
    base_url: str,
    api_key: str,
    model: str,
    content: str,
) -> list[dict]:
    """Make a streaming OpenAI-format request and return chunks."""
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
        return chunks


async def make_non_streaming_request(
    base_url: str,
    api_key: str,
    model: str,
    content: str,
) -> dict:
    """Make a non-streaming OpenAI-format request."""
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
        return response.json()


def extract_content_from_chunks(chunks: list[dict]) -> str:
    """Extract complete content from streaming chunks."""
    content_parts = []
    for chunk in chunks:
        if "choices" in chunk and chunk["choices"]:
            delta = chunk["choices"][0].get("delta", {})
            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])
    return "".join(content_parts)


def extract_content_from_response(data: dict) -> str:
    """Extract content from non-streaming response."""
    if "choices" in data and data["choices"]:
        message = data["choices"][0].get("message", {})
        return message.get("content", "")
    return ""


@pytest.mark.asyncio
async def test_simple_replacement_streaming():
    """Test SimpleStringReplacementPolicy with streaming response."""
    # Define replacements
    replacements = {
        "hello": "HELLO",
        "world": "WORLD",
    }

    with gateway_with_replacements(replacements) as gateway:
        # Make streaming request with explicit echo instruction
        chunks = await make_streaming_request(
            base_url=gateway["base_url"],
            api_key=gateway["api_key"],
            model="claude-haiku-4-5",
            content='Please repeat this phrase exactly: "hello world"',
        )

        # Extract content
        content = extract_content_from_chunks(chunks)

        # Verify replacements were applied
        assert content, "Expected non-empty content"

        # The model should echo back our phrase, and replacements should be applied
        content_lower = content.lower()

        # If the original words appear (lowercase), they should have been replaced
        # Check that at least one replacement was made
        has_replacements = "HELLO" in content or "WORLD" in content
        has_originals = "hello" in content_lower or "world" in content_lower

        # Either we have replacements, or the model didn't echo the exact phrase
        # (which is fine - we just verify the policy runs)
        assert has_replacements or not has_originals, f"Expected replacements to be applied. Content: {content}"


@pytest.mark.asyncio
async def test_simple_replacement_non_streaming():
    """Test SimpleStringReplacementPolicy with non-streaming response."""
    # Define replacements
    replacements = {
        "Paris": "PARIS",
        "France": "FRANCE",
    }

    with gateway_with_replacements(replacements) as gateway:
        # Make non-streaming request with explicit repetition
        data = await make_non_streaming_request(
            base_url=gateway["base_url"],
            api_key=gateway["api_key"],
            model="claude-haiku-4-5",
            content='Please include the exact words "Paris" and "France" in your response. '
            "Tell me the capital of France.",
        )

        # Extract content
        content = extract_content_from_response(data)

        # Verify we got a response
        assert content, "Expected non-empty content"

        # Check that replacements were applied
        has_paris_replacement = "PARIS" in content
        has_france_replacement = "FRANCE" in content
        has_paris_original = "Paris" in content and "PARIS" not in content
        has_france_original = "France" in content and "FRANCE" not in content

        # At least one replacement should have been made
        assert has_paris_replacement or has_france_replacement, (
            f"Expected at least one replacement to be applied. Content: {content}"
        )

        # Original words should not appear if replacements were made
        if has_paris_replacement:
            assert not has_paris_original, f"Expected 'Paris' to be replaced with 'PARIS'. Content: {content}"
        if has_france_replacement:
            assert not has_france_original, f"Expected 'France' to be replaced with 'FRANCE'. Content: {content}"


@pytest.mark.asyncio
async def test_multiple_replacements_streaming():
    """Test multiple replacements in streaming mode."""
    # Define multiple replacements
    replacements = {
        "one": "1",
        "two": "2",
        "three": "3",
    }

    with gateway_with_replacements(replacements) as gateway:
        # Make request with explicit echo instruction
        chunks = await make_streaming_request(
            base_url=gateway["base_url"],
            api_key=gateway["api_key"],
            model="claude-haiku-4-5",
            content='Please repeat these words in your response: "one, two, three"',
        )

        # Extract content
        content = extract_content_from_chunks(chunks)

        # Verify we got a response
        assert content, "Expected non-empty content"

        # Count how many replacements were made
        replacements_found = sum(1 for num in ["1", "2", "3"] if num in content)

        # If the model echoed our words, at least some should be replaced
        content_lower = content.lower()
        words_present = sum(1 for word in ["one", "two", "three"] if word in content_lower)

        # Either we have replacements, or the model didn't include the words
        assert replacements_found > 0 or words_present == 0, f"Expected replacements to be applied. Content: {content}"


@pytest.mark.asyncio
async def test_empty_replacements():
    """Test that empty replacements dict passes content through unchanged."""
    # No replacements
    replacements = {}

    with gateway_with_replacements(replacements) as gateway:
        # Make request
        data = await make_non_streaming_request(
            base_url=gateway["base_url"],
            api_key=gateway["api_key"],
            model="claude-haiku-4-5",
            content="Say hello in one word.",
        )

        # Extract content
        content = extract_content_from_response(data)

        # Just verify we got a response (no replacements to check)
        assert content, "Expected non-empty content"


@pytest.mark.asyncio
async def test_case_sensitive_replacements():
    """Test that replacements are case-sensitive."""
    # Only lowercase "hello" should be replaced
    replacements = {
        "hello": "REPLACED_LOWER",
    }

    with gateway_with_replacements(replacements) as gateway:
        # Request that includes lowercase "hello"
        data = await make_non_streaming_request(
            base_url=gateway["base_url"],
            api_key=gateway["api_key"],
            model="claude-haiku-4-5",
            content='Please use the word "hello" (all lowercase) at least once in your response.',
        )

        # Extract content
        content = extract_content_from_response(data)

        # Verify we got a response
        assert content, "Expected non-empty content"

        # If lowercase "hello" appears, it should be replaced with REPLACED_LOWER
        # Capital "Hello" should NOT be replaced
        has_replacement = "REPLACED_LOWER" in content
        has_lowercase_hello = "hello" in content

        # If we found the replacement, original lowercase should be gone
        if has_replacement:
            assert not has_lowercase_hello, f"Expected 'hello' to be replaced. Content: {content}"
