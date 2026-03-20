"""Mock e2e tests for OnboardingPolicy — first turn gets welcome, subsequent turns pass through.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_onboarding_policy.py -v
"""

import json

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_ONBOARDING_POLICY = "luthien_proxy.policies.onboarding_policy:OnboardingPolicy"
_ONBOARDING_CONFIG = {"gateway_url": "http://localhost:8000"}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

_FIRST_TURN_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100,
}

_SECOND_TURN_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
    ],
    "max_tokens": 100,
}


@pytest.mark.asyncio
async def test_first_turn_appends_welcome(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """First turn response includes the onboarding welcome message."""
    mock_anthropic.enqueue(text_response("Hi there! How can I help?"))

    async with policy_context(_ONBOARDING_POLICY, _ONBOARDING_CONFIG):
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                headers=_HEADERS,
                json=_FIRST_TURN_REQUEST,
            )

    assert resp.status_code == 200
    body = resp.json()
    content_texts = [b["text"] for b in body["content"] if b["type"] == "text"]
    all_text = " ".join(content_texts)

    assert "Hi there! How can I help?" in all_text
    assert "Welcome to Luthien" in all_text
    assert "policy-config" in all_text


@pytest.mark.asyncio
async def test_second_turn_passthrough(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """Second turn response passes through without the welcome message."""
    mock_anthropic.enqueue(text_response("I'm doing great, thanks!"))

    async with policy_context(_ONBOARDING_POLICY, _ONBOARDING_CONFIG):
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                headers=_HEADERS,
                json=_SECOND_TURN_REQUEST,
            )

    assert resp.status_code == 200
    body = resp.json()
    content_texts = [b["text"] for b in body["content"] if b["type"] == "text"]
    all_text = " ".join(content_texts)

    assert "I'm doing great, thanks!" in all_text
    assert "Welcome to Luthien" not in all_text


@pytest.mark.asyncio
async def test_first_turn_streaming_appends_welcome(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
):
    """First turn streaming response includes welcome message events."""
    mock_anthropic.enqueue(text_response("Hello from streaming!"))

    async with policy_context(_ONBOARDING_POLICY, _ONBOARDING_CONFIG):
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                headers=_HEADERS,
                json={**_FIRST_TURN_REQUEST, "stream": True},
            )

    assert resp.status_code == 200

    # Collect all text from SSE events
    all_text = ""
    for line in resp.text.split("\n"):
        if line.startswith("data: ") and line.strip() != "data: [DONE]":
            try:
                event = json.loads(line[6:])
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    all_text += delta.get("text", "")
                elif event.get("type") == "content_block_start":
                    block = event.get("content_block", {})
                    all_text += block.get("text", "")
            except json.JSONDecodeError:
                pass

    assert "Hello from streaming!" in all_text
    assert "Welcome to Luthien" in all_text
