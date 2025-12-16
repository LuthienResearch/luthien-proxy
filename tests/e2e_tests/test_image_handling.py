"""E2E tests for image/multimodal handling across API format combinations.

Test Matrix (non-streaming only - streaming is more complex for multimodal):
- Anthropic client → Anthropic backend (native format)
- Anthropic client → OpenAI backend (Anthropic→OpenAI conversion)
- OpenAI client → OpenAI backend (native format)
- OpenAI client → Anthropic backend (OpenAI→Anthropic conversion)

These tests verify that images pass through the proxy without validation errors.
Note: There's a known issue (#108) where Claude may respond to wrong image content
even when validation passes - these tests verify the proxy doesn't reject the request.
"""

import base64
import os
from pathlib import Path

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))

# Minimal 1x1 red PNG image (68 bytes) for testing
TINY_RED_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# The test image contains the text "This is not a pipe."
EXPECTED_IMAGE_TEXT = "This is not a pipe."


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        yield client


def _load_test_image_base64() -> str:
    """Load test image and return as base64 string."""
    image_path = FIXTURES_DIR / "test_image.png"
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _normalize_text(s: str) -> str:
    """Normalize text for comparison (lowercase, strip punctuation/whitespace)."""
    return s.lower().replace(".", "").replace('"', "").replace("'", "").strip()


# === Anthropic Client Tests ===

ANTHROPIC_ENDPOINT = f"{GATEWAY_URL}/v1/messages"
ANTHROPIC_BACKENDS = ["claude-haiku-4-5", "gpt-4o-mini"]


def _anthropic_image_content(image_b64: str, prompt: str) -> list:
    """Build Anthropic-format message content with image."""
    return [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": image_b64},
        },
        {"type": "text", "text": prompt},
    ]


def _assert_anthropic_response(data: dict) -> None:
    """Assert valid Anthropic response structure."""
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert len(data["content"]) > 0
    assert "text" in data["content"][0]


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("backend_model", ANTHROPIC_BACKENDS)
async def test_anthropic_client_image_passthrough(http_client, backend_model: str):
    """E2E: Anthropic client with image passes through proxy without errors."""
    response = await http_client.post(
        ANTHROPIC_ENDPOINT,
        json={
            "model": backend_model,
            "messages": [
                {
                    "role": "user",
                    "content": _anthropic_image_content(
                        TINY_RED_PNG_BASE64, "What color is this image? Reply with just the color name."
                    ),
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    _assert_anthropic_response(response.json())


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("backend_model", ANTHROPIC_BACKENDS)
async def test_anthropic_client_semantic_image(http_client, backend_model: str):
    """E2E Semantic: Verify LLM sees correct image content via Anthropic client."""
    image_b64 = _load_test_image_base64()

    response = await http_client.post(
        ANTHROPIC_ENDPOINT,
        json={
            "model": backend_model,
            "messages": [
                {
                    "role": "user",
                    "content": _anthropic_image_content(
                        image_b64, "Read the text in this image and repeat it back exactly, with no other commentary."
                    ),
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    response_text = data["content"][0]["text"]

    assert _normalize_text(EXPECTED_IMAGE_TEXT) in _normalize_text(response_text), (
        f"Model should read the text from the image. Expected: '{EXPECTED_IMAGE_TEXT}' Got: '{response_text}'"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_image_in_history(http_client):
    """E2E: Verify image in Anthropic conversation history doesn't break subsequent requests.

    This was the original bug in #103 - after sending an image, subsequent
    messages would fail because the image was in the history.
    """
    content = _anthropic_image_content(TINY_RED_PNG_BASE64, "What is this?")

    # First message with image
    first_response = await http_client.post(
        ANTHROPIC_ENDPOINT,
        json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert first_response.status_code == 200, f"First request failed: {first_response.text}"
    assistant_reply = first_response.json()["content"][0]["text"]

    # Second message with image still in history
    second_response = await http_client.post(
        ANTHROPIC_ENDPOINT,
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {"role": "user", "content": content},
                {"role": "assistant", "content": assistant_reply},
                {"role": "user", "content": "Thanks! Can you tell me more?"},
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert second_response.status_code == 200, f"Second request failed: {second_response.text}"
    _assert_anthropic_response(second_response.json())


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_text_only_content_blocks(http_client):
    """E2E: Verify text-only content blocks still work after image handling changes.

    Regression test to ensure the image handling didn't break regular text messages
    that use the content block format.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Say 'hello' and nothing else."}],
                }
            ],
            "max_tokens": 20,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    assert data["type"] == "message"
    assert len(data["content"]) > 0


# === OpenAI Client Tests ===

OPENAI_ENDPOINT = f"{GATEWAY_URL}/v1/chat/completions"
OPENAI_BACKENDS = ["gpt-4o-mini", "claude-haiku-4-5"]


def _openai_image_content(image_b64: str, prompt: str) -> list:
    """Build OpenAI-format message content with image."""
    return [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        {"type": "text", "text": prompt},
    ]


def _assert_openai_response(data: dict) -> None:
    """Assert valid OpenAI response structure."""
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("backend_model", OPENAI_BACKENDS)
async def test_openai_client_image_passthrough(http_client, backend_model: str):
    """E2E: OpenAI client with image passes through proxy without errors."""
    response = await http_client.post(
        OPENAI_ENDPOINT,
        json={
            "model": backend_model,
            "messages": [
                {
                    "role": "user",
                    "content": _openai_image_content(
                        TINY_RED_PNG_BASE64, "What color is this image? Reply with just the color name."
                    ),
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    _assert_openai_response(response.json())


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("backend_model", OPENAI_BACKENDS)
async def test_openai_client_semantic_image(http_client, backend_model: str):
    """E2E Semantic: Verify LLM sees correct image content via OpenAI client."""
    image_b64 = _load_test_image_base64()

    response = await http_client.post(
        OPENAI_ENDPOINT,
        json={
            "model": backend_model,
            "messages": [
                {
                    "role": "user",
                    "content": _openai_image_content(
                        image_b64, "Read the text in this image and repeat it back exactly, with no other commentary."
                    ),
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    response_text = data["choices"][0]["message"]["content"]

    assert _normalize_text(EXPECTED_IMAGE_TEXT) in _normalize_text(response_text), (
        f"Model should read the text from the image. Expected: '{EXPECTED_IMAGE_TEXT}' Got: '{response_text}'"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_image_in_history(http_client):
    """E2E: Verify image in OpenAI conversation history doesn't break subsequent requests."""
    content = _openai_image_content(TINY_RED_PNG_BASE64, "What is this?")

    # First message with image
    first_response = await http_client.post(
        OPENAI_ENDPOINT,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert first_response.status_code == 200, f"First request failed: {first_response.text}"
    assistant_reply = first_response.json()["choices"][0]["message"]["content"]

    # Second message with image still in history
    second_response = await http_client.post(
        OPENAI_ENDPOINT,
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": content},
                {"role": "assistant", "content": assistant_reply},
                {"role": "user", "content": "Thanks! Can you tell me more?"},
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert second_response.status_code == 200, f"Second request failed: {second_response.text}"
    _assert_openai_response(second_response.json())
