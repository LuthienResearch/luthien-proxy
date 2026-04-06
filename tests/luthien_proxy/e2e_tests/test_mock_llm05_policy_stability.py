"""Mock e2e tests for LLM05: Policy Setup Stability.

Verify that policy setup via the admin API completes without 500 errors or error
loops, and that the gateway remains stable after rapid policy switches.
Trello: https://trello.com/c/lx5CHHi8/1102

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_llm05_policy_stability.py -v
"""

import asyncio

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = [pytest.mark.mock_e2e, pytest.mark.llm05]

_ADMIN_POLICY_SET_PATH = "/api/admin/policy/set"
_ADMIN_POLICY_GET_PATH = "/api/admin/policy"

_NOOP = "luthien_proxy.policies.noop_policy:NoOpPolicy"
_ALL_CAPS = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
_DEBUG_LOGGING = "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy"
_STRING_REPLACEMENT = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"


async def _set_policy(client: httpx.AsyncClient, gateway_url: str, admin_api_key: str, class_ref: str, config: dict):
    return await client.post(
        f"{gateway_url}{_ADMIN_POLICY_SET_PATH}",
        headers={"Authorization": f"Bearer {admin_api_key}"},
        json={"policy_class_ref": class_ref, "config": config, "enabled_by": "e2e-test"},
    )


@pytest.mark.asyncio
async def test_policy_set_returns_200(
    gateway_healthy,
    gateway_url,
    admin_api_key,
):
    """Setting a valid policy via admin API returns 200, not 500."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await _set_policy(client, gateway_url, admin_api_key, _NOOP, {})

    assert response.status_code == 200, f"Policy set returned {response.status_code}: {response.text}"


@pytest.mark.asyncio
async def test_rapid_policy_switches_no_500(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Rapid policy switches do not cause 500 errors on subsequent requests."""
    policies = [
        (_NOOP, {}),
        (_ALL_CAPS, {}),
        (_DEBUG_LOGGING, {}),
        (_STRING_REPLACEMENT, {"replacements": [["hello", "hi"]]}),
        (_NOOP, {}),
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for class_ref, config in policies:
            set_resp = await _set_policy(client, gateway_url, admin_api_key, class_ref, config)
            assert set_resp.status_code == 200, (
                f"Policy switch to {class_ref} failed: {set_resp.status_code}: {set_resp.text}"
            )

    # After all switches, a normal request must succeed
    mock_anthropic.enqueue(text_response("still stable"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
            headers=auth_headers,
        )

    assert response.status_code == 200, (
        f"Gateway unstable after rapid policy switches: {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_policy_get_after_set_reflects_new_policy(
    gateway_healthy,
    gateway_url,
    admin_api_key,
):
    """GET /api/admin/policy reflects the policy that was just set."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        await _set_policy(client, gateway_url, admin_api_key, _ALL_CAPS, {})
        get_resp = await client.get(
            f"{gateway_url}{_ADMIN_POLICY_GET_PATH}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
        )

    assert get_resp.status_code == 200, f"Policy GET failed: {get_resp.status_code}: {get_resp.text}"
    body = get_resp.json()
    assert "AllCapsPolicy" in str(body), f"Expected AllCapsPolicy in GET response: {body}"


@pytest.mark.asyncio
async def test_policy_context_restores_noop_after_test(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """policy_context restores NoOpPolicy after the context exits — no lingering state."""
    mock_anthropic.enqueue(text_response("inside context"))
    mock_anthropic.enqueue(text_response("outside context"))

    async with policy_context(_ALL_CAPS, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            inside_resp = await client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )
        assert inside_resp.status_code == 200

    # After context exits, NoOpPolicy should be active — response passes through unchanged
    async with httpx.AsyncClient(timeout=15.0) as client:
        outside_resp = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
            headers=auth_headers,
        )

    assert outside_resp.status_code == 200, (
        f"Gateway unstable after policy_context exit: {outside_resp.status_code}: {outside_resp.text}"
    )
    body = outside_resp.json()
    content_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    # NoOpPolicy passes through unchanged — text should NOT be all-caps
    assert content_text == "outside context", f"Expected passthrough after context exit, got: {content_text!r}"


@pytest.mark.asyncio
async def test_concurrent_requests_during_policy_switch_no_500(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Concurrent requests during a policy switch do not produce 500 errors."""
    for _ in range(3):
        mock_anthropic.enqueue(text_response("concurrent ok"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fire 3 requests and a policy switch concurrently
        results = await asyncio.gather(
            client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            ),
            client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            ),
            _set_policy(client, gateway_url, admin_api_key, _DEBUG_LOGGING, {}),
            client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            ),
            return_exceptions=True,
        )

    for result in results:
        assert not isinstance(result, BaseException), f"Concurrent operation raised: {result}"
        assert isinstance(result, httpx.Response)
        assert result.status_code != 500, (
            f"Got 500 during concurrent policy switch: {result.status_code}: {result.text}"
        )
