"""Mock e2e tests for the inference-provider admin API.

Validates create -> list -> delete against a running gateway. Does NOT
invoke a real backend provider — the `/ping` endpoint that exercises the
full chain lands in PR #5 alongside the policy-test UI.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_inference_providers.py -v
"""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.mock_e2e


@pytest.mark.asyncio
async def test_inference_provider_crud_roundtrip(
    gateway_healthy,  # noqa: ARG001 - fixture ensures gateway is up
    gateway_url: str,
    admin_headers: dict,
):
    """POST a provider, see it in the list, delete it, confirm it's gone."""
    name = f"e2e-provider-{uuid.uuid4().hex[:8]}"
    body = {
        "name": name,
        "backend_type": "direct_api",
        "credential_name": None,
        "default_model": "claude-sonnet-4-6",
        "config": {"api_base": "https://api.anthropic.com"},
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Create
        create_resp = await client.post(
            f"{gateway_url}/api/admin/inference-providers",
            headers=admin_headers,
            json=body,
        )
        assert create_resp.status_code == 200, create_resp.text
        assert create_resp.json() == {"success": True, "name": name}

        # List
        list_resp = await client.get(
            f"{gateway_url}/api/admin/inference-providers",
            headers=admin_headers,
        )
        assert list_resp.status_code == 200, list_resp.text
        listed = list_resp.json()
        matching = [p for p in listed["providers"] if p["name"] == name]
        assert len(matching) == 1
        provider = matching[0]
        assert provider["backend_type"] == "direct_api"
        assert provider["default_model"] == "claude-sonnet-4-6"
        assert provider["config"] == {"api_base": "https://api.anthropic.com"}

        # Delete
        delete_resp = await client.delete(
            f"{gateway_url}/api/admin/inference-providers/{name}",
            headers=admin_headers,
        )
        assert delete_resp.status_code == 200, delete_resp.text

        # Gone
        list_after = await client.get(
            f"{gateway_url}/api/admin/inference-providers",
            headers=admin_headers,
        )
        assert list_after.status_code == 200
        assert all(p["name"] != name for p in list_after.json()["providers"])


@pytest.mark.asyncio
async def test_inference_provider_unknown_backend_returns_400(
    gateway_healthy,  # noqa: ARG001
    gateway_url: str,
    admin_headers: dict,
):
    """An unknown backend_type is rejected with 400, not silently stored."""
    body = {
        "name": f"e2e-bad-{uuid.uuid4().hex[:8]}",
        "backend_type": "not_a_real_backend",
        "credential_name": None,
        "default_model": "m",
        "config": {},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{gateway_url}/api/admin/inference-providers",
            headers=admin_headers,
            json=body,
        )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_inference_provider_delete_missing_returns_404(
    gateway_healthy,  # noqa: ARG001
    gateway_url: str,
    admin_headers: dict,
):
    """Deleting a non-existent provider returns 404."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(
            f"{gateway_url}/api/admin/inference-providers/nonexistent-xyz",
            headers=admin_headers,
        )
    assert resp.status_code == 404, resp.text
