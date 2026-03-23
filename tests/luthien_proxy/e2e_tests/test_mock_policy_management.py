"""Mock e2e tests for admin policy management API lifecycle.

Verifies:
- Querying the current active policy
- Switching policies via the admin API
- Rejecting invalid policy references
- Confirming that a newly activated policy takes effect on the next request

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_policy_management.py -v
"""

import asyncio

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import ADMIN_API_KEY, API_KEY, GATEWAY_URL
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.mock_e2e

_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}
_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

_NOOP_CLASS_REF = "luthien_proxy.policies.noop_policy:NoOpPolicy"
_ALL_CAPS_CLASS_REF = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"

_BASE_REQUEST = {
    "model": "claude-haiku-4-5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 100,
    "stream": False,
}


@pytest.mark.asyncio
async def test_get_current_policy_returns_policy_info(gateway_healthy):
    """GET /api/admin/policy/current returns a JSON body with a non-empty policy class reference."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/policy/current",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()

    # The response should include a field that identifies the policy class
    # API returns: {"policy": "...", "class_ref": "...", "enabled_at": ..., "enabled_by": ..., "config": {}}
    policy_ref = data.get("class_ref") or data.get("policy_class_ref") or data.get("class") or ""
    assert policy_ref, f"Expected a non-empty policy class ref in response, got: {data}"


@pytest.mark.asyncio
async def test_set_policy_changes_active_policy(gateway_healthy):
    """Setting AllCapsPolicy via admin API is reflected in the current policy response."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Activate AllCapsPolicy
        set_response = await client.post(
            f"{GATEWAY_URL}/api/admin/policy/set",
            headers=_ADMIN_HEADERS,
            json={
                "policy_class_ref": _ALL_CAPS_CLASS_REF,
                "config": {},
                "enabled_by": "e2e-test",
            },
        )
        assert set_response.status_code == 200, f"Failed to set policy: {set_response.text}"
        assert set_response.json().get("success"), f"Policy set returned failure: {set_response.text}"

        await asyncio.sleep(0.3)

        # Confirm current policy reflects AllCapsPolicy
        current_response = await client.get(
            f"{GATEWAY_URL}/api/admin/policy/current",
            headers=_ADMIN_HEADERS,
        )
        assert current_response.status_code == 200
        current_text = current_response.text
        assert "AllCapsPolicy" in current_text or "all_caps_policy" in current_text, (
            f"Expected AllCapsPolicy in current policy response, got: {current_text}"
        )

        # Restore NoOp
        restore_response = await client.post(
            f"{GATEWAY_URL}/api/admin/policy/set",
            headers=_ADMIN_HEADERS,
            json={
                "policy_class_ref": _NOOP_CLASS_REF,
                "config": {},
                "enabled_by": "e2e-test-cleanup",
            },
        )
        assert restore_response.status_code == 200


@pytest.mark.asyncio
async def test_set_invalid_policy_returns_error(gateway_healthy):
    """Attempting to set a non-existent policy class returns a non-200 response or success=false."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/api/admin/policy/set",
            headers=_ADMIN_HEADERS,
            json={
                "policy_class_ref": "nonexistent.module:FakePolicy",
                "config": {},
                "enabled_by": "e2e-test",
            },
        )

    # Either a non-200 HTTP status or a body indicating failure
    if response.status_code == 200:
        data = response.json()
        assert not data.get("success"), f"Expected success=false for invalid policy, got: {data}"
    else:
        assert response.status_code >= 400, (
            f"Expected error status for invalid policy, got {response.status_code}: {response.text}"
        )


@pytest.mark.asyncio
async def test_policy_takes_effect_on_next_request(mock_anthropic: MockAnthropicServer, gateway_healthy):
    """A newly activated AllCapsPolicy transforms the response text on the very next request."""
    mock_anthropic.enqueue(text_response("hello from the assistant"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Activate AllCapsPolicy
        set_response = await client.post(
            f"{GATEWAY_URL}/api/admin/policy/set",
            headers=_ADMIN_HEADERS,
            json={
                "policy_class_ref": _ALL_CAPS_CLASS_REF,
                "config": {},
                "enabled_by": "e2e-test",
            },
        )
        assert set_response.status_code == 200
        assert set_response.json().get("success")

        await asyncio.sleep(0.3)

        # Make a regular request — text should be uppercased
        msg_response = await client.post(
            f"{GATEWAY_URL}/v1/messages",
            json=_BASE_REQUEST,
            headers=_HEADERS,
        )
        assert msg_response.status_code == 200, f"Request failed: {msg_response.text}"
        text = msg_response.json()["content"][0]["text"]
        assert text == text.upper(), f"Expected uppercased text, got: {text!r}"

        # Restore NoOp
        restore_response = await client.post(
            f"{GATEWAY_URL}/api/admin/policy/set",
            headers=_ADMIN_HEADERS,
            json={
                "policy_class_ref": _NOOP_CLASS_REF,
                "config": {},
                "enabled_by": "e2e-test-cleanup",
            },
        )
        assert restore_response.status_code == 200
