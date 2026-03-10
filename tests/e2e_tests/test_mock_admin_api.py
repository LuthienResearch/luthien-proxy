"""Mock e2e tests for admin API endpoints.

Verifies:
- GET /api/admin/models returns a list of models
- GET /api/admin/auth/config returns auth configuration
- GET /api/admin/auth/credentials returns cached credentials list
- GET /api/admin/telemetry returns telemetry configuration
- GET /api/admin/policy/list returns available policy classes
- All admin endpoints reject non-admin API keys

Requires:
  - Gateway running with mock backend:
      docker compose -f docker-compose.yaml -f docker-compose.mock.yaml up -d
  - Mock server auto-started by the mock_anthropic fixture (port 18888).

Run:
    uv run pytest -m mock_e2e tests/e2e_tests/test_mock_admin_api.py -v
"""

import httpx
import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, API_KEY, GATEWAY_URL

pytestmark = pytest.mark.mock_e2e

_ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_API_KEY}"}
_REGULAR_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


@pytest.mark.asyncio
async def test_list_models_returns_model_list(gateway_healthy):
    """GET /api/admin/models returns 200 with a non-empty list of models."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/models",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "models" in data, f"Expected 'models' key in response, got: {data}"
    assert isinstance(data["models"], list), f"Expected 'models' to be a list, got: {type(data['models'])}"
    assert len(data["models"]) > 0, f"Expected non-empty models list, got: {data['models']}"


@pytest.mark.asyncio
async def test_list_models_requires_admin_auth(gateway_healthy):
    """GET /api/admin/models with a regular API key returns 401 or 403."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/models",
            headers=_REGULAR_HEADERS,
        )

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for non-admin key, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_get_auth_config_returns_config(gateway_healthy):
    """GET /api/admin/auth/config returns 200 with an auth_mode field."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/auth/config",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "auth_mode" in data, f"Expected 'auth_mode' field in response, got: {data}"
    assert isinstance(data["auth_mode"], str), f"Expected 'auth_mode' to be a string, got: {type(data['auth_mode'])}"


@pytest.mark.asyncio
async def test_get_auth_config_requires_admin_auth(gateway_healthy):
    """GET /api/admin/auth/config with a regular API key returns 401 or 403."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/auth/config",
            headers=_REGULAR_HEADERS,
        )

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for non-admin key, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_get_credentials_returns_list(gateway_healthy):
    """GET /api/admin/auth/credentials returns 200 with 'credentials' list and 'count' int."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/auth/credentials",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "credentials" in data, f"Expected 'credentials' key in response, got: {data}"
    assert "count" in data, f"Expected 'count' key in response, got: {data}"
    assert isinstance(data["credentials"], list), (
        f"Expected 'credentials' to be a list, got: {type(data['credentials'])}"
    )
    assert isinstance(data["count"], int), f"Expected 'count' to be an int, got: {type(data['count'])}"


@pytest.mark.asyncio
async def test_get_credentials_requires_admin_auth(gateway_healthy):
    """GET /api/admin/auth/credentials with a regular API key returns 401 or 403."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/auth/credentials",
            headers=_REGULAR_HEADERS,
        )

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for non-admin key, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_get_telemetry_config_returns_config(gateway_healthy):
    """GET /api/admin/telemetry returns 200 with an 'enabled' boolean field."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/telemetry",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "enabled" in data, f"Expected 'enabled' field in response, got: {data}"
    assert isinstance(data["enabled"], bool), f"Expected 'enabled' to be a bool, got: {type(data['enabled'])}"


@pytest.mark.asyncio
async def test_get_telemetry_config_requires_admin_auth(gateway_healthy):
    """GET /api/admin/telemetry with a regular API key returns 401 or 403."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/telemetry",
            headers=_REGULAR_HEADERS,
        )

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for non-admin key, got {response.status_code}: {response.text}"
    )


@pytest.mark.asyncio
async def test_policy_list_returns_available_policies(gateway_healthy):
    """GET /api/admin/policy/list returns 200 with a 'policies' list including known policies."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/policy/list",
            headers=_ADMIN_HEADERS,
        )

    assert response.status_code == 200, f"Unexpected status: {response.status_code}: {response.text}"
    data = response.json()
    assert "policies" in data, f"Expected 'policies' key in response, got: {data}"
    assert isinstance(data["policies"], list), f"Expected 'policies' to be a list, got: {type(data['policies'])}"
    assert len(data["policies"]) > 0, f"Expected non-empty policies list, got: {data['policies']}"

    # Each item should have a class_ref field
    first = data["policies"][0]
    assert "class_ref" in first, f"Expected 'class_ref' in policy item, got: {first}"

    # The list should include at least one well-known policy
    all_refs = " ".join(p.get("class_ref", "") + " " + p.get("name", "") for p in data["policies"])
    assert "NoOpPolicy" in all_refs or "AllCapsPolicy" in all_refs, (
        f"Expected at least NoOpPolicy or AllCapsPolicy in policy list, got refs: {all_refs}"
    )


@pytest.mark.asyncio
async def test_policy_list_requires_admin_auth(gateway_healthy):
    """GET /api/admin/policy/list with a regular API key returns 401 or 403."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/api/admin/policy/list",
            headers=_REGULAR_HEADERS,
        )

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for non-admin key, got {response.status_code}: {response.text}"
    )
