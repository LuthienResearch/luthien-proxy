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

import os

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))

# Minimal 1x1 red PNG image (68 bytes) for testing
# This is a valid PNG that models can process
TINY_RED_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        yield client


# === Anthropic Client API with Images ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_anthropic_backend_with_image(http_client):
    """E2E: Anthropic client with image → Anthropic backend (claude-haiku).

    Tests native Anthropic image format passes through without modification.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": TINY_RED_PNG_BASE64,
                            },
                        },
                        {"type": "text", "text": "What color is this image? Reply with just the color name."},
                    ],
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()

    # Verify Anthropic response structure
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert len(data["content"]) > 0
    assert "text" in data["content"][0]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_client_openai_backend_with_image(http_client):
    """E2E: Anthropic client with image → OpenAI backend (gpt-4o-mini).

    Tests Anthropic→OpenAI image format conversion.
    Uses gpt-4o-mini which supports vision.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": TINY_RED_PNG_BASE64,
                            },
                        },
                        {"type": "text", "text": "What color is this image? Reply with just the color name."},
                    ],
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()

    # Verify Anthropic response structure (converted from OpenAI)
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert len(data["content"]) > 0
    assert "text" in data["content"][0]


# === OpenAI Client API with Images ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_openai_backend_with_image(http_client):
    """E2E: OpenAI client with image → OpenAI backend (gpt-4o-mini).

    Tests native OpenAI image format passes through without modification.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{TINY_RED_PNG_BASE64}"},
                        },
                        {"type": "text", "text": "What color is this image? Reply with just the color name."},
                    ],
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()

    # Verify OpenAI response structure
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_client_anthropic_backend_with_image(http_client):
    """E2E: OpenAI client with image → Anthropic backend (claude-haiku).

    Tests OpenAI image format handling when routed to Anthropic.
    The proxy should pass through OpenAI format to LiteLLM which handles conversion.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{TINY_RED_PNG_BASE64}"},
                        },
                        {"type": "text", "text": "What color is this image? Reply with just the color name."},
                    ],
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()

    # Verify OpenAI response structure
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) > 0
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]


# === Image in Conversation History ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_anthropic_image_in_history(http_client):
    """E2E: Verify image in conversation history doesn't break subsequent requests.

    This was the original bug in #103 - after sending an image, subsequent
    messages would fail because the image was in the history.
    """
    # First message with image
    first_response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": TINY_RED_PNG_BASE64,
                            },
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert first_response.status_code == 200, f"First request failed: {first_response.text}"
    first_data = first_response.json()
    assistant_reply = first_data["content"][0]["text"]

    # Second message with image still in history
    second_response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": TINY_RED_PNG_BASE64,
                            },
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                },
                {"role": "assistant", "content": assistant_reply},
                {"role": "user", "content": "Thanks! Can you tell me more?"},
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert second_response.status_code == 200, f"Second request failed: {second_response.text}"
    second_data = second_response.json()
    assert second_data["type"] == "message"
    assert len(second_data["content"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_openai_image_in_history(http_client):
    """E2E: Verify image in conversation history works for OpenAI format."""
    # First message with image
    first_response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{TINY_RED_PNG_BASE64}"},
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                }
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert first_response.status_code == 200, f"First request failed: {first_response.text}"
    first_data = first_response.json()
    assistant_reply = first_data["choices"][0]["message"]["content"]

    # Second message with image still in history
    second_response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{TINY_RED_PNG_BASE64}"},
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                },
                {"role": "assistant", "content": assistant_reply},
                {"role": "user", "content": "Thanks! Can you tell me more?"},
            ],
            "max_tokens": 50,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert second_response.status_code == 200, f"Second request failed: {second_response.text}"
    second_data = second_response.json()
    assert second_data["object"] == "chat.completion"
    assert len(second_data["choices"]) > 0


# === Text-only Content Blocks (Regression) ===


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


# === Semantic Validation Tests ===
# These tests verify the LLM actually sees the correct image content,
# not just that the request passes validation.

# 100x100 PNG with 4 colored quadrants:
# Top-left: Red, Top-right: Green, Bottom-left: Blue, Bottom-right: Yellow
# This distinctive pattern lets us verify the model sees the actual image.
COLOR_QUADRANTS_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAGQAAABkCAIAAAD/gAIDAAAAoElEQVR42u3QwQkAMBDDsOy/dDrD"
    "/VIQeACjNBksFSxYsGDBgiVYsGDBggVLsGDBggULlmDBggULFizBggULFixYggULFixYsAQLFixY"
    "sGAJFixYsGDB0u9Ym19tBoMFCxYsWLBgwYIFCxYsWLBgwYIFCxYsWLBgwYIFCxYsWLBgwYIFCxYs"
    "WLBgwYIFCxYsWLBgwYIFCxYsWLBgwYJ16AG7GKX9cdhSlQAAAABJRU5ErkJggg=="
)


def _normalize_color(text: str) -> set[str]:
    """Extract color words from response text for flexible matching."""
    text_lower = text.lower()
    colors_found = set()
    color_variants = {
        "red": ["red", "crimson", "scarlet"],
        "green": ["green", "lime"],
        "blue": ["blue", "azure"],
        "yellow": ["yellow", "gold"],
    }
    for canonical, variants in color_variants.items():
        if any(v in text_lower for v in variants):
            colors_found.add(canonical)
    return colors_found


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_semantic_anthropic_client_anthropic_backend(http_client):
    """E2E Semantic: Verify Claude sees the correct image through Anthropic endpoint.

    Uses a 4-quadrant color image and asks the model to identify all colors.
    This validates the image data is correctly transmitted, not just that
    the request format is valid.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": COLOR_QUADRANTS_PNG_BASE64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "This image has 4 colored quadrants. List the 4 colors you see, one per line.",
                        },
                    ],
                }
            ],
            "max_tokens": 100,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    response_text = data["content"][0]["text"]

    # Verify the model identified the expected colors
    colors_found = _normalize_color(response_text)
    expected_colors = {"red", "green", "blue", "yellow"}

    assert len(colors_found & expected_colors) >= 3, (
        f"Model should identify at least 3 of the 4 quadrant colors. "
        f"Expected: {expected_colors}, Found: {colors_found}. "
        f"Response: {response_text}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_semantic_openai_client_openai_backend(http_client):
    """E2E Semantic: Verify GPT sees the correct image through OpenAI endpoint.

    Uses a 4-quadrant color image and asks the model to identify all colors.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{COLOR_QUADRANTS_PNG_BASE64}"},
                        },
                        {
                            "type": "text",
                            "text": "This image has 4 colored quadrants. List the 4 colors you see, one per line.",
                        },
                    ],
                }
            ],
            "max_tokens": 100,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    response_text = data["choices"][0]["message"]["content"]

    colors_found = _normalize_color(response_text)
    expected_colors = {"red", "green", "blue", "yellow"}

    assert len(colors_found & expected_colors) >= 3, (
        f"Model should identify at least 3 of the 4 quadrant colors. "
        f"Expected: {expected_colors}, Found: {colors_found}. "
        f"Response: {response_text}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_semantic_anthropic_client_openai_backend(http_client):
    """E2E Semantic: Verify image survives Anthropic→OpenAI format conversion.

    Sends Anthropic-format image request to OpenAI backend (gpt-4o-mini).
    Tests that the format conversion preserves image data correctly.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/messages",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": COLOR_QUADRANTS_PNG_BASE64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "This image has 4 colored quadrants. List the 4 colors you see, one per line.",
                        },
                    ],
                }
            ],
            "max_tokens": 100,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    response_text = data["content"][0]["text"]

    colors_found = _normalize_color(response_text)
    expected_colors = {"red", "green", "blue", "yellow"}

    assert len(colors_found & expected_colors) >= 3, (
        f"Model should identify at least 3 of the 4 quadrant colors. "
        f"Expected: {expected_colors}, Found: {colors_found}. "
        f"Response: {response_text}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_semantic_openai_client_anthropic_backend(http_client):
    """E2E Semantic: Verify image survives OpenAI→Anthropic format conversion.

    Sends OpenAI-format image request to Anthropic backend (claude-haiku).
    Tests that LiteLLM's format conversion preserves image data correctly.
    """
    response = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        json={
            "model": "claude-haiku-4-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{COLOR_QUADRANTS_PNG_BASE64}"},
                        },
                        {
                            "type": "text",
                            "text": "This image has 4 colored quadrants. List the 4 colors you see, one per line.",
                        },
                    ],
                }
            ],
            "max_tokens": 100,
            "stream": False,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )

    assert response.status_code == 200, f"Request failed: {response.status_code} - {response.text}"
    data = response.json()
    response_text = data["choices"][0]["message"]["content"]

    colors_found = _normalize_color(response_text)
    expected_colors = {"red", "green", "blue", "yellow"}

    assert len(colors_found & expected_colors) >= 3, (
        f"Model should identify at least 3 of the 4 quadrant colors. "
        f"Expected: {expected_colors}, Found: {colors_found}. "
        f"Response: {response_text}"
    )
