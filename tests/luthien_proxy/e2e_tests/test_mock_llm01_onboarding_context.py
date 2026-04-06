"""Mock e2e tests for LLM01: Onboarding Context.

Verify Claude knows what Luthien is after onboarding policy is active.
Trello: https://trello.com/c/p9YJcdCV/1098

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_llm01_onboarding_context.py -v
"""

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = [pytest.mark.mock_e2e, pytest.mark.llm01]

_ONBOARDING_POLICY = "luthien_proxy.policies.onboarding_policy:OnboardingPolicy"
_ONBOARDING_CONFIG = {"gateway_url": "http://localhost:8000"}

_FIRST_TURN = {**BASE_REQUEST, "messages": [{"role": "user", "content": "What is Luthien?"}]}
_SECOND_TURN = {
    **BASE_REQUEST,
    "messages": [
        {"role": "user", "content": "What is Luthien?"},
        {"role": "assistant", "content": "Luthien is an AI control proxy."},
        {"role": "user", "content": "Tell me more."},
    ],
}


@pytest.mark.asyncio
async def test_onboarding_context_injected_on_first_turn(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """First turn with OnboardingPolicy active includes Luthien context in the response."""
    mock_anthropic.enqueue(text_response("I can help you with that."))

    async with policy_context(
        _ONBOARDING_POLICY, _ONBOARDING_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_FIRST_TURN, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    all_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    assert "Luthien" in all_text


@pytest.mark.asyncio
async def test_onboarding_context_includes_setup_hint(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Onboarding context includes a setup hint (policy-config or gateway URL)."""
    mock_anthropic.enqueue(text_response("Here to help."))

    async with policy_context(
        _ONBOARDING_POLICY, _ONBOARDING_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_FIRST_TURN, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    all_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    assert "policy-config" in all_text or "localhost:8000" in all_text


@pytest.mark.asyncio
async def test_onboarding_context_not_repeated_on_second_turn(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Onboarding context is only injected on the first turn, not on subsequent turns."""
    mock_anthropic.enqueue(text_response("Follow-up response."))

    async with policy_context(
        _ONBOARDING_POLICY, _ONBOARDING_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_SECOND_TURN, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200
    body = response.json()
    all_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    assert "Welcome to Luthien" not in all_text


@pytest.mark.asyncio
async def test_onboarding_context_response_is_200(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """OnboardingPolicy does not cause 4xx/5xx errors on first turn."""
    mock_anthropic.enqueue(text_response("Hello!"))

    async with policy_context(
        _ONBOARDING_POLICY, _ONBOARDING_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**_FIRST_TURN, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    assert response.json()["type"] == "message"
