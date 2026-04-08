"""Mock e2e tests for UAT05: Policy Setup Stability.

Verify that policy setup via the admin API completes without 500 errors or error
loops, and that the gateway remains stable after rapid policy switches.
Trello: https://trello.com/c/lx5CHHi8/1102

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_uat05_policy_stability.py -v
"""

import asyncio

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = [pytest.mark.mock_e2e, pytest.mark.uat_stability]

_ADMIN_POLICY_SET_PATH = "/api/admin/policy/set"
_ADMIN_POLICY_GET_PATH = "/api/admin/policy"

_NOOP = "luthien_proxy.policies.noop_policy:NoOpPolicy"
_ALL_CAPS = "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
_DEBUG_LOGGING = "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy"
_STRING_REPLACEMENT = "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"


async def _set_policy(client: httpx.AsyncClient, gateway_url: str, admin_api_key: str, class_ref: str, config: dict):
    """Fire-and-forget policy set (no activation polling — unlike conftest.set_policy)."""
    return await client.post(
        f"{gateway_url}{_ADMIN_POLICY_SET_PATH}",
        headers={"Authorization": f"Bearer {admin_api_key}"},
        json={"policy_class_ref": class_ref, "config": config, "enabled_by": "e2e-test"},
    )


@pytest.mark.asyncio
async def test_policy_set_returns_200_and_activates(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Setting a valid policy via admin API returns 200 and the policy serves requests."""
    mock_anthropic.enqueue(text_response("noop passthrough"))

    async with policy_context(_NOOP, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    content_text = " ".join(b["text"] for b in body["content"] if b["type"] == "text")
    assert "noop passthrough" in content_text, f"Policy didn't serve request: {content_text!r}"


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
        (_ALL_CAPS, {}),
        (_DEBUG_LOGGING, {}),
        (_STRING_REPLACEMENT, {"replacements": [["hello", "hi"]]}),
    ]

    async with policy_context(_NOOP, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
            for class_ref, config in policies:
                set_resp = await _set_policy(client, gateway_url, admin_api_key, class_ref, config)
                assert set_resp.status_code == 200, (
                    f"Policy switch to {class_ref} failed: {set_resp.status_code}: {set_resp.text}"
                )

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
        body = response.json()
        assert any(b.get("text") == "still stable" for b in body.get("content", [])), (
            f"Expected proxied mock content 'still stable', got: {body.get('content')}"
        )


@pytest.mark.asyncio
async def test_policy_get_after_set_reflects_new_policy(
    gateway_healthy,
    gateway_url,
    admin_api_key,
):
    """GET /api/admin/policy reflects the policy that was just set."""
    async with policy_context(_ALL_CAPS, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=15.0) as client:
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
    assert content_text == "outside context", f"Expected passthrough after context exit, got: {content_text!r}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        policy_resp = await client.get(
            f"{gateway_url}{_ADMIN_POLICY_GET_PATH}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
        )

    assert policy_resp.status_code == 200
    assert "NoOpPolicy" in str(policy_resp.json()), (
        f"Expected NoOpPolicy to be active after context exit, got: {policy_resp.json()}"
    )


@pytest.mark.asyncio
async def test_batch_requests_during_policy_switch_no_500(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Concurrent requests during a policy switch do not produce 500 errors.

    asyncio.gather is best-effort concurrency — ordering is not guaranteed.
    This test catches crashes and 500s, not race conditions requiring true parallelism.
    3 responses are enqueued for 3 POST requests; _set_policy hits the admin API,
    not /v1/messages, so it does not consume from the mock queue.
    """
    for _ in range(3):
        mock_anthropic.enqueue(text_response("concurrent ok"))
    async with policy_context(_NOOP, {}, gateway_url=gateway_url, admin_api_key=admin_api_key):
        async with httpx.AsyncClient(timeout=30.0) as client:
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
            assert result.status_code == 200, f"Got {result.status_code} during concurrent policy switch: {result.text}"
