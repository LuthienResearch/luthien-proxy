"""Mock e2e tests for LLM03: Unimplemented Policies.

Verify that activating a non-existent or misconfigured policy class gives a clear
error response rather than a silent 500 crash or an error loop.
Trello: https://trello.com/c/IUxmYwSW/1100

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_llm03_unimplemented_policies.py -v
"""

import httpx
import pytest
from tests.luthien_proxy.e2e_tests.conftest import BASE_REQUEST, policy_context
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = [pytest.mark.mock_e2e, pytest.mark.uat_unimplemented]

_ADMIN_POLICY_SET_PATH = "/api/admin/policy/set"

_NONEXISTENT_POLICY = "luthien_proxy.policies.does_not_exist:GhostPolicy"
_INVALID_MODULE = "not.a.real.module:FakePolicy"


@pytest.mark.asyncio
async def test_nonexistent_policy_class_returns_error(
    gateway_healthy,
    gateway_url,
    admin_api_key,
):
    """Activating a non-existent policy class via admin API returns a clear error, not 500."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}{_ADMIN_POLICY_SET_PATH}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            json={
                "policy_class_ref": _NONEXISTENT_POLICY,
                "config": {},
                "enabled_by": "e2e-test",
            },
        )

    # Must not be a 500 — the gateway should handle this gracefully
    assert response.status_code != 500, f"Non-existent policy class caused a 500: {response.text}"
    assert response.status_code in (400, 422), (
        f"Expected 400/422 for non-existent policy class, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_invalid_module_path_returns_error(
    gateway_healthy,
    gateway_url,
    admin_api_key,
):
    """Activating a policy with an invalid module path returns a clear error."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}{_ADMIN_POLICY_SET_PATH}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            json={
                "policy_class_ref": _INVALID_MODULE,
                "config": {},
                "enabled_by": "e2e-test",
            },
        )

    assert response.status_code != 500, f"Invalid module path caused a 500: {response.text}"
    assert response.status_code in (400, 422), (
        f"Expected 400/422 for invalid module path, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_gateway_responsive_after_bad_policy_set(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """Gateway remains responsive for normal requests after a failed policy set attempt."""
    # Attempt to set a non-existent policy (will fail)
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{gateway_url}{_ADMIN_POLICY_SET_PATH}",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            json={
                "policy_class_ref": _NONEXISTENT_POLICY,
                "config": {},
                "enabled_by": "e2e-test",
            },
        )

    # Gateway must still serve normal requests after the failed set
    mock_anthropic.enqueue(text_response("still working"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/v1/messages",
            json={**BASE_REQUEST, "stream": False},
            headers=auth_headers,
        )

    assert response.status_code == 200, (
        f"Gateway unresponsive after bad policy set: {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_hackathon_policy_template_activates(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url,
    auth_headers,
    admin_api_key,
):
    """HackathonPolicy template activates without crashing (it's a valid no-op stub)."""
    mock_anthropic.enqueue(text_response("response from hackathon policy"))

    async with policy_context(
        "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy",
        {},
        gateway_url=gateway_url,
        admin_api_key=admin_api_key,
    ):
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{gateway_url}/v1/messages",
                json={**BASE_REQUEST, "stream": False},
                headers=auth_headers,
            )

    assert response.status_code == 200, (
        f"HackathonPolicy template caused an error: {response.status_code}: {response.text}"
    )
