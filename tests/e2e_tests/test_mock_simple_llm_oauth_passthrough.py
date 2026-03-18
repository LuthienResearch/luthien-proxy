"""Mock e2e test for SimpleLLMPolicy OAuth passthrough authentication.

Verifies that when the client authenticates with an OAuth bearer token
(a Bearer token that is NOT a regular sk-ant-api* key), the judge call:
  1. Forwards the OAuth token as the api_key
  2. Adds the required 'anthropic-beta: oauth-2025-04-20' header

Setup:
- Auth mode set to 'passthrough' without credential validation so the
  gateway accepts a fake OAuth token without hitting Anthropic.
- Judge api_base points at the mock server.
- Mock server receives both requests and we inspect their headers.

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_simple_llm_oauth_passthrough.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import GATEWAY_URL, auth_config_context, policy_context
from tests.e2e_tests.mock_anthropic.responses import text_response
from tests.e2e_tests.mock_anthropic.server import DEFAULT_MOCK_PORT, MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_SIMPLE_LLM_POLICY = "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"

# Simulated OAuth bearer token — does NOT start with sk-ant-api,
# so _judge_oauth_headers() treats it as OAuth and adds the beta header.
_OAUTH_TOKEN = "claude-oauth-bearer-token-for-e2e-testing"

_JUDGE_CONFIG = {
    "instructions": "Pass all content through",
    "model": "claude-haiku-4-5",
    "api_base": f"http://host.docker.internal:{DEFAULT_MOCK_PORT}",
    "on_error": "pass",
}

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "say hello"}],
    "max_tokens": 50,
    "stream": False,
}


@pytest.mark.asyncio
async def test_judge_adds_oauth_header_for_oauth_bearer_token(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """Judge call includes anthropic-beta: oauth-2025-04-20 when client uses an OAuth token.

    The mock server receives two requests:
    1. Main LLM call — uses gateway's ANTHROPIC_API_KEY (mock-key), no OAuth header
    2. Judge call — uses the OAuth token + anthropic-beta: oauth-2025-04-20 header
    """
    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with auth_config_context("passthrough", validate_credentials=False):
        async with policy_context(_SIMPLE_LLM_POLICY, _JUDGE_CONFIG):
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{GATEWAY_URL}/v1/messages",
                    json=_BASE_REQUEST,
                    headers={"Authorization": f"Bearer {_OAUTH_TOKEN}"},
                )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, (
        f"Expected 2 requests (main + judge), got {len(all_headers)}. Requests: {mock_anthropic.received_requests()}"
    )

    # Main call: gateway uses AnthropicClient(auth_token=token), which sends
    # Authorization: Bearer <token> + anthropic-beta: oauth-2025-04-20.
    # The token is NOT in x-api-key (that's LiteLLM's style, not the Anthropic SDK's).
    main_headers = all_headers[0]
    assert main_headers.get("x-api-key") != _OAUTH_TOKEN, (
        "Main call should not send OAuth token via x-api-key (it uses Authorization: Bearer)"
    )

    # Judge call: LiteLLM sends token via x-api-key, our code adds the OAuth beta header.
    judge_headers = all_headers[1]
    assert judge_headers.get("x-api-key") == _OAUTH_TOKEN, (
        f"Judge call should forward OAuth token as x-api-key, got: {judge_headers.get('x-api-key')!r}"
    )
    assert "oauth-2025-04-20" in judge_headers.get("anthropic-beta", ""), (
        f"Judge call should include anthropic-beta: oauth-2025-04-20, "
        f"got anthropic-beta: {judge_headers.get('anthropic-beta')!r}"
    )


@pytest.mark.asyncio
async def test_oauth_only_no_server_key(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """Judge works using only the OAuth token — no server api key, no policy api key.

    The mock environment has ANTHROPIC_API_KEY=mock-key, but LLM_JUDGE_API_KEY is not
    set, so _fallback_api_key is None. Without passthrough the judge would fall through
    to LiteLLM's ANTHROPIC_API_KEY env var. With OAuth passthrough the judge uses the
    OAuth token directly and ignores the env fallback.
    """
    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with auth_config_context("passthrough", validate_credentials=False):
        async with policy_context(_SIMPLE_LLM_POLICY, _JUDGE_CONFIG):
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{GATEWAY_URL}/v1/messages",
                    json=_BASE_REQUEST,
                    headers={"Authorization": f"Bearer {_OAUTH_TOKEN}"},
                )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, f"Expected 2 requests, got {len(all_headers)}"

    judge_headers = all_headers[1]
    # OAuth token was used — judge did not fall through to the env-var key
    assert judge_headers.get("x-api-key") == _OAUTH_TOKEN, (
        f"Judge should use OAuth token even with no server key configured, "
        f"got x-api-key: {judge_headers.get('x-api-key')!r}"
    )
    assert "oauth-2025-04-20" in judge_headers.get("anthropic-beta", ""), "Judge call should have OAuth beta header"


@pytest.mark.asyncio
async def test_oauth_takes_precedence_over_server_env_key(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """OAuth passthrough takes precedence over the server's ANTHROPIC_API_KEY env fallback.

    In the mock env, ANTHROPIC_API_KEY=mock-key is set. Without any explicit key or
    passthrough, LiteLLM would use 'mock-key' for the judge call. With an OAuth bearer
    token in the request, the passthrough key wins and the judge uses the OAuth token.
    """
    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with auth_config_context("passthrough", validate_credentials=False):
        async with policy_context(_SIMPLE_LLM_POLICY, _JUDGE_CONFIG):
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{GATEWAY_URL}/v1/messages",
                    json=_BASE_REQUEST,
                    headers={"Authorization": f"Bearer {_OAUTH_TOKEN}"},
                )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, f"Expected 2 requests, got {len(all_headers)}"

    judge_headers = all_headers[1]
    # OAuth token wins over ANTHROPIC_API_KEY=mock-key
    assert judge_headers.get("x-api-key") == _OAUTH_TOKEN, (
        f"OAuth passthrough should beat server env key (mock-key), got x-api-key: {judge_headers.get('x-api-key')!r}"
    )
    assert judge_headers.get("x-api-key") != "mock-key", (
        "Server ANTHROPIC_API_KEY should NOT be used when OAuth passthrough is available"
    )


@pytest.mark.asyncio
async def test_regular_api_key_does_not_get_oauth_header(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
) -> None:
    """A regular Anthropic API key (sk-ant-api*) does NOT get the OAuth beta header."""
    api_key_token = "sk-ant-api03-regular-api-key-not-oauth"

    mock_anthropic.enqueue(text_response("Hello there"))
    mock_anthropic.enqueue(text_response('{"action": "pass", "blocks": []}'))

    async with auth_config_context("passthrough", validate_credentials=False):
        async with policy_context(_SIMPLE_LLM_POLICY, _JUDGE_CONFIG):
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{GATEWAY_URL}/v1/messages",
                    json=_BASE_REQUEST,
                    headers={"Authorization": f"Bearer {api_key_token}"},
                )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    all_headers = mock_anthropic.received_request_headers()
    assert len(all_headers) == 2, f"Expected 2 requests, got {len(all_headers)}"

    judge_headers = all_headers[1]
    assert judge_headers.get("x-api-key") == api_key_token, (
        f"Judge should forward the API key, got: {judge_headers.get('x-api-key')!r}"
    )
    assert "oauth-2025-04-20" not in judge_headers.get("anthropic-beta", ""), (
        "Regular Anthropic API key should NOT trigger the OAuth beta header"
    )
