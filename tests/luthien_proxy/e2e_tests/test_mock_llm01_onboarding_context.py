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
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST, collect_sse_text, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

from luthien_proxy.policies.onboarding_policy import WELCOME_MESSAGE

pytestmark = [pytest.mark.mock_e2e, pytest.mark.uat_onboarding]

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

_WELCOME_TITLE: str = next((line.strip() for line in WELCOME_MESSAGE.splitlines() if "Welcome to Luthien" in line), "")
_WELCOME_SETUP_HINT: str = next((line.strip() for line in WELCOME_MESSAGE.splitlines() if "policy-config" in line), "")


@pytest.fixture(scope="session", autouse=True)
def _assert_welcome_message_shape():
    assert _WELCOME_TITLE, "WELCOME_MESSAGE no longer contains 'Welcome to Luthien' — update test constants"
    assert _WELCOME_SETUP_HINT, "WELCOME_MESSAGE no longer contains 'policy-config' — update test constants"


@pytest.mark.asyncio
async def test_onboarding_context_injected_on_first_turn(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """OnboardingPolicy appends WELCOME_MESSAGE to the response on first turn.

    The mock backend returns a canned reply; OnboardingPolicy appends the welcome
    block via on_anthropic_response. Assertions check the injected response text.
    """
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

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    assert response.json()["type"] == "message"
    body = response.json()
    all_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    assert "Welcome to Luthien" in all_text
    assert "policy-config" in all_text


@pytest.mark.asyncio
async def test_onboarding_context_injected_on_first_turn_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """OnboardingPolicy appends WELCOME_MESSAGE in the streaming path too."""
    mock_anthropic.enqueue(text_response("I can help you with that."))

    async with policy_context(
        _ONBOARDING_POLICY, _ONBOARDING_CONFIG, gateway_url=gateway_url, admin_api_key=admin_api_key
    ):
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/messages",
                json={**_FIRST_TURN, "stream": True},
                headers=auth_headers,
            ) as response:
                assert response.status_code == 200, f"Expected 200, got {response.status_code}"
                all_text = await collect_sse_text(response)

    assert "Welcome to Luthien" in all_text
    assert "policy-config" in all_text


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
    assert _WELCOME_TITLE not in all_text, f"Second turn should not contain the welcome title. Got: {all_text!r}"
    assert _WELCOME_SETUP_HINT not in all_text, f"Second turn should not contain the setup hint. Got: {all_text!r}"
