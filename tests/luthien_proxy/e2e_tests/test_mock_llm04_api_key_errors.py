"""Mock e2e tests for LLM04: API Key Error Messaging.

Verify that invalid API key errors return a human-readable Anthropic-format error
body rather than a nested JSON blob or raw exception dump.
Trello: https://trello.com/c/CrtFRTuS/1101

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_llm04_api_key_errors.py -v
"""

import os

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = [pytest.mark.mock_e2e, pytest.mark.llm04]

AUTH_MODE = os.getenv("AUTH_MODE", "both")


@pytest.mark.asyncio
async def test_missing_auth_returns_human_readable_error(
    gateway_healthy,
    gateway_url,
):
    """Missing Authorization header returns a human-readable error, not a raw exception."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
        )

    assert response.status_code in (401, 403), f"Expected 401/403 for missing auth, got {response.status_code}"
    # Must be parseable JSON
    body = response.json()
    # Must not be a raw Python exception dump
    assert "Traceback" not in response.text, "Response contains a raw Python traceback"
    assert "Exception" not in response.text or body.get("type") == "error", "Response looks like a raw exception dump"


@pytest.mark.asyncio
async def test_invalid_key_error_is_anthropic_format(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
):
    """Invalid API key error body follows the Anthropic error envelope format."""
    if AUTH_MODE == "both":
        # In 'both' mode, unknown keys are forwarded as passthrough — enqueue a response
        mock_anthropic.enqueue(text_response("passthrough"))

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
            headers={"Authorization": "Bearer sk-this-is-not-a-valid-key"},
        )

    if AUTH_MODE == "both":
        # Passthrough mode — key forwarded upstream, not rejected by gateway
        assert response.status_code == 200
        return

    assert response.status_code == 401, f"Expected 401 for invalid key, got {response.status_code}: {response.text}"
    body = response.json()
    # Must be Anthropic error envelope: {"type": "error", "error": {"type": ..., "message": ...}}
    assert body.get("type") == "error", f"Expected Anthropic error envelope, got: {body}"
    assert "error" in body, f"Missing 'error' field in response: {body}"
    assert "message" in body["error"], f"Missing 'message' in error field: {body['error']}"
    # Message must be a plain string, not a nested JSON blob
    assert isinstance(body["error"]["message"], str), f"Error message is not a string: {type(body['error']['message'])}"
    assert "{" not in body["error"]["message"][:50], (
        f"Error message looks like nested JSON: {body['error']['message'][:100]}"
    )


@pytest.mark.asyncio
async def test_missing_auth_error_has_message_field(
    gateway_healthy,
    gateway_url,
):
    """Missing auth error body contains a human-readable 'message' field."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
        )

    assert response.status_code in (401, 403)
    body = response.json()
    # Must have some form of human-readable message
    has_message = (
        isinstance(body.get("message"), str)
        or isinstance(body.get("detail"), str)
        or (isinstance(body.get("error"), dict) and isinstance(body["error"].get("message"), str))
    )
    assert has_message, f"No human-readable message field found in error response: {body}"


@pytest.mark.asyncio
async def test_wrong_admin_key_returns_clear_error(
    gateway_healthy,
    gateway_url,
):
    """Wrong admin API key returns a clear error, not a 500."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url}/api/admin/policy/set",
            headers={"Authorization": "Bearer wrong-admin-key"},
            json={
                "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
                "config": {},
                "enabled_by": "e2e-test",
            },
        )

    assert response.status_code in (401, 403), (
        f"Expected 401/403 for wrong admin key, got {response.status_code}: {response.text}"
    )
    assert response.status_code != 500, "Wrong admin key caused a 500"
