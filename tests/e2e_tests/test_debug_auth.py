# ABOUTME: E2E tests for debug endpoint authentication
# ABOUTME: Tests that /debug/* endpoints require admin authentication

"""E2E tests for debug endpoint authentication.

Tests that all debug endpoints require admin authentication:
- GET /api/api/debug/calls - List recent calls
- GET /api/api/debug/calls/{call_id} - Get events for a call
- GET /api/api/debug/calls/{call_id}/diff - Get diff for a call

These tests require:
- Running gateway service (docker compose up gateway)
- Valid ADMIN_API_KEY in environment
"""

import os

import httpx
import pytest

# === Test Configuration ===

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))


@pytest.fixture
async def http_client():
    """Provide async HTTP client for e2e tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture
def admin_headers():
    """Provide admin authentication headers."""
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture
def x_api_key_headers():
    """Provide x-api-key authentication headers."""
    return {"x-api-key": ADMIN_API_KEY}


# === Debug Endpoint Authentication Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_calls_requires_authentication(http_client):
    """Test that GET /api/debug/calls requires authentication."""
    response = await http_client.get(f"{GATEWAY_URL}/api/debug/calls")
    assert response.status_code == 403, "Should reject request without auth"

    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 403, "Should reject request with wrong key"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_calls_accepts_valid_bearer_token(http_client, admin_headers):
    """Test that GET /api/debug/calls accepts valid Bearer token."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls",
        headers=admin_headers,
    )
    # May be 200 (with data) or 503 (no DB), but not 403
    assert response.status_code != 403, f"Should accept valid admin key: {response.text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_calls_accepts_x_api_key(http_client, x_api_key_headers):
    """Test that GET /api/debug/calls accepts x-api-key header."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls",
        headers=x_api_key_headers,
    )
    # May be 200 (with data) or 503 (no DB), but not 403
    assert response.status_code != 403, f"Should accept x-api-key: {response.text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_call_events_requires_authentication(http_client):
    """Test that GET /api/debug/calls/{call_id} requires authentication."""
    response = await http_client.get(f"{GATEWAY_URL}/api/debug/calls/test-call-id")
    assert response.status_code == 403, "Should reject request without auth"

    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls/test-call-id",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 403, "Should reject request with wrong key"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_call_events_accepts_valid_key(http_client, admin_headers):
    """Test that GET /api/debug/calls/{call_id} accepts valid admin key."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls/test-call-id",
        headers=admin_headers,
    )
    # May be 404 (call not found) or 503 (no DB), but not 403
    assert response.status_code != 403, f"Should accept valid admin key: {response.text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_call_diff_requires_authentication(http_client):
    """Test that GET /api/debug/calls/{call_id}/diff requires authentication."""
    response = await http_client.get(f"{GATEWAY_URL}/api/debug/calls/test-call-id/diff")
    assert response.status_code == 403, "Should reject request without auth"

    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls/test-call-id/diff",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 403, "Should reject request with wrong key"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_debug_call_diff_accepts_valid_key(http_client, admin_headers):
    """Test that GET /api/debug/calls/{call_id}/diff accepts valid admin key."""
    response = await http_client.get(
        f"{GATEWAY_URL}/api/debug/calls/test-call-id/diff",
        headers=admin_headers,
    )
    # May be 404 (call not found) or 503 (no DB), but not 403
    assert response.status_code != 403, f"Should accept valid admin key: {response.text}"
