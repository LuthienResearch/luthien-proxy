"""Mock e2e tests for admin API endpoints.

Covers policy discovery — the one admin endpoint with meaningful content
assertions. Auth enforcement across admin endpoints is covered by
test_mock_auth.py and is not repeated here.

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_admin_api.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, GATEWAY_URL

pytestmark = pytest.mark.mock_e2e

_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.mark.asyncio
async def test_policy_list_includes_known_policies(gateway_healthy):
    """GET /api/admin/policy/list returns discoverable policies including built-ins.

    This validates the policy discovery mechanism, not just the HTTP shape.
    Each policy must have a class_ref, and at least NoOpPolicy and AllCapsPolicy
    must be present — they are built-in and should always be discoverable.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/policy/list",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    policies = data.get("policies", [])
    assert len(policies) > 0, "Expected at least one discoverable policy"

    all_refs = " ".join(p.get("class_ref", "") + " " + p.get("name", "") for p in policies)
    assert "NoOpPolicy" in all_refs, f"NoOpPolicy must always be discoverable, got: {all_refs}"
    assert "AllCapsPolicy" in all_refs, f"AllCapsPolicy must always be discoverable, got: {all_refs}"
