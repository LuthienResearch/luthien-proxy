"""Mock e2e tests for LLM04: API Key Error Messaging.

Verify that invalid API key errors return a human-readable Anthropic-format error
body rather than a nested JSON blob or raw exception dump.
Trello: https://trello.com/c/CrtFRTuS/1101

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_llm04_api_key_errors.py -v
"""

import json
import os

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = [pytest.mark.mock_e2e, pytest.mark.uat_api_key]


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
    body = response.json()
    assert "Traceback" not in response.text, "Response contains a raw Python traceback"
    if "Exception" in response.text:
        assert body.get("type") == "error", (
            f"Response text contains 'Exception' but is not an Anthropic error envelope: {body}"
        )


@pytest.mark.asyncio
async def test_invalid_key_in_passthrough_mode_returns_valid_message(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
):
    """In AUTH_MODE=both, an unknown key is forwarded as passthrough and returns a valid Anthropic message."""
    if os.getenv("AUTH_MODE", "both") != "both":
        pytest.skip("This test covers AUTH_MODE=both passthrough behavior")

    mock_anthropic.enqueue(text_response("passthrough response"))

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
            headers={"Authorization": "Bearer sk-this-is-not-a-valid-key"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body.get("type") == "message", f"Expected message response in passthrough mode, got: {body}"
    assert "Traceback" not in response.text, "Response contains a raw Python traceback"


@pytest.mark.asyncio
async def test_invalid_key_in_strict_mode_returns_anthropic_error_envelope(
    gateway_healthy,
    gateway_url,
):
    """In AUTH_MODE=proxy_key, an invalid key returns an Anthropic error envelope with a plain string message."""
    if os.getenv("AUTH_MODE", "both") == "both":
        pytest.skip("This test covers AUTH_MODE=proxy_key strict rejection behavior")

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
            headers={"Authorization": "Bearer sk-this-is-not-a-valid-key"},
        )

    assert response.status_code == 401, f"Expected 401 for invalid key, got {response.status_code}: {response.text}"
    body = response.json()
    assert body.get("type") == "error", f"Expected Anthropic error envelope, got: {body}"
    assert "error" in body, f"Missing 'error' field in response: {body}"
    assert "message" in body["error"], f"Missing 'message' in error field: {body['error']}"
    assert isinstance(body["error"]["message"], str), f"Error message is not a string: {type(body['error']['message'])}"
    try:
        json.loads(body["error"]["message"])
        pytest.fail(f"Error message is double-encoded JSON: {body['error']['message'][:100]}")
    except (ValueError, TypeError):
        pass


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
