"""Mock e2e tests for SimpleLLMPolicy passthrough authentication.

Verifies that when no explicit api_key is set on the policy, the client's
auth token is forwarded to judge LLM calls.

How this works in the mock_e2e setup:
- Gateway has ANTHROPIC_API_KEY=mock-key (used for main LLM calls via LiteLLM env var)
- Client authenticates with API_KEY (= PROXY_API_KEY = "sk-luthien-dev-key")
- Judge api_base points at the mock server (same as main LLM)
- Main LLM call arrives at mock server with mock-key
- Judge call arrives at mock server with the client's passthrough key (sk-luthien-dev-key)
- We verify the judge request headers contain the client's key, not mock-key

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_simple_llm_passthrough_auth.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import DEFAULT_MOCK_PORT, MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_SIMPLE_LLM_POLICY = "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"

# Judge pointed at the mock server, no explicit api_key → passthrough is used.
# host.docker.internal resolves to the host machine from inside the gateway container,
# which is where the mock Anthropic server runs.
_PASSTHROUGH_JUDGE_CONFIG = {
    "instructions": "Pass all content through",
    "model": "claude-haiku-4-5",
    "api_base": f"http://host.docker.internal:{DEFAULT_MOCK_PORT}",
    # Deliberately no api_key — should use client's passthrough key
    "on_error": "pass",
}

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "say hello"}],
    "max_tokens": 50,
    "stream": False,
}

_AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.asyncio
async def test_judge_uses_passthrough_key_non_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """Judge call uses client's passthrough API key when no policy key is set.

    The mock server receives two requests:
    1. Main LLM call — uses gateway's ANTHROPIC_API_KEY env var (mock-key)
    2. Judge call — uses client's passthrough key (API_KEY = sk-luthien-dev-key)

    We verify the judge call carries the client's key, not mock-key.
    """
    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with policy_context(_SIMPLE_LLM_POLICY, _PASSTHROUGH_JUDGE_CONFIG):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_AUTH_HEADER,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, (
        f"Expected 2 requests (main + judge), got {len(all_headers)}. "
        f"Requests: {mock_anthropic.received_requests()}"
    )

    # Main call uses gateway's configured ANTHROPIC_API_KEY (not client's key)
    main_call_key = all_headers[0].get("x-api-key", "")
    assert main_call_key != API_KEY, (
        f"Main call should use gateway's key, not client passthrough key, got: {main_call_key!r}"
    )

    # Judge call uses the client's passthrough key
    judge_call_key = all_headers[1].get("x-api-key", "")
    assert judge_call_key == API_KEY, (
        f"Judge call should use client's passthrough key ({API_KEY!r}), got: {judge_call_key!r}"
    )


@pytest.mark.asyncio
async def test_judge_uses_passthrough_key_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """Same passthrough key behavior in streaming mode."""
    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with policy_context(_SIMPLE_LLM_POLICY, _PASSTHROUGH_JUDGE_CONFIG):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json={**_BASE_REQUEST, "stream": True},
                headers=_AUTH_HEADER,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, (
        f"Expected 2 requests (main + judge), got {len(all_headers)}"
    )

    judge_call_key = all_headers[1].get("x-api-key", "")
    assert judge_call_key == API_KEY, (
        f"Judge call should use client passthrough key ({API_KEY!r}), got: {judge_call_key!r}"
    )


@pytest.mark.asyncio
async def test_explicit_policy_key_overrides_passthrough(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """When an explicit api_key is set on the policy, it takes priority over passthrough."""
    explicit_key = "explicit-policy-api-key-overrides-passthrough"
    config_with_explicit_key = {
        **_PASSTHROUGH_JUDGE_CONFIG,
        "api_key": explicit_key,
    }

    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with policy_context(_SIMPLE_LLM_POLICY, config_with_explicit_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1/messages",
                json=_BASE_REQUEST,
                headers=_AUTH_HEADER,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, f"Expected 2 requests, got {len(all_headers)}"

    judge_call_key = all_headers[1].get("x-api-key", "")
    assert judge_call_key == explicit_key, (
        f"Judge should use explicit policy key ({explicit_key!r}), got: {judge_call_key!r}"
    )
    assert judge_call_key != API_KEY, (
        f"Judge should NOT use passthrough key when explicit key is set"
    )
