"""Mock e2e tests for gateway authentication enforcement.

Verifies that the gateway enforces authentication before proxying requests to
the backend. The mock Anthropic server ignores auth headers — all assertions
here target the gateway's own auth layer.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_auth.py -v
"""

import os

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

AUTH_MODE = os.getenv("AUTH_MODE", "both")

pytestmark = pytest.mark.mock_e2e

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}


@pytest.mark.asyncio
async def test_missing_auth_header_returns_401(gateway_healthy, gateway_url):
    """A request with no Authorization header is rejected by the gateway."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json=_BASE_REQUEST,
            # No Authorization header
        )

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for missing auth, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_wrong_api_key_returns_401(mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url):
    """A request with an incorrect API key is rejected (proxy_key) or treated as passthrough (both)."""
    # AUTH_MODE=both treats unrecognised keys as passthrough API keys forwarded to the backend
    if AUTH_MODE == "both":
        mock_anthropic.enqueue(text_response("passthrough response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json=_BASE_REQUEST,
            headers={"Authorization": "Bearer wrong-key"},
        )

    if AUTH_MODE == "both":
        assert response.status_code == 200, (
            f"AUTH_MODE=both should forward unknown keys as passthrough, got {response.status_code}"
        )
        assert mock_anthropic.last_request() is not None, "Backend should receive passthrough request"
    else:
        assert response.status_code in (401, 403), (
            f"Expected 401 or 403 for wrong key, got {response.status_code}: {response.text}"
        )


@pytest.mark.asyncio
async def test_valid_api_key_succeeds(mock_anthropic: MockAnthropicServer, gateway_healthy, gateway_url, api_key):
    """A request with the correct API key is proxied and returns 200."""
    mock_anthropic.enqueue(text_response("authenticated response"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json=_BASE_REQUEST,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 200, f"Expected 200 for valid key, got {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_admin_endpoint_accessible_on_localhost(gateway_healthy, gateway_url, api_key):
    """Admin endpoints are accessible without admin key on localhost (LOCALHOST_AUTH_BYPASS by design).

    PR #405 intentionally extended LOCALHOST_AUTH_BYPASS to cover /api/admin/* routes.
    Local installations bypass auth entirely — this is the expected behavior for
    single-user local deployments. Auth is enforced in production (non-localhost) mode.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{gateway_url}/api/admin/policy/current",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 200, (
        f"Admin endpoint should be accessible on localhost, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_admin_endpoint_accepts_admin_key(gateway_healthy, gateway_url, admin_api_key):
    """The admin policy endpoint accepts requests authenticated with the admin API key."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{gateway_url}/api/admin/policy/current",
            headers={"Authorization": f"Bearer {admin_api_key}"},
        )

    assert response.status_code == 200, f"Expected 200 for admin key, got {response.status_code}: {response.text}"


# NOTE: A test for "admin endpoints require auth in non-localhost mode" is not
# implemented here because mock_e2e tests run against an out-of-process gateway,
# making in-process patching of is_localhost_request ineffective.
# To test this properly: add localhost_auth_bypass to the admin settings API
# (PUT /api/admin/gateway/settings), then toggle it off, assert 401, toggle back.
# Alternatively, implement as a sqlite_e2e test where the gateway runs in-process.
