"""Mock e2e tests for the admin config dashboard API.

Regression coverage for the `GET /api/admin/config` + `PUT/DELETE
/api/admin/config/{key}` endpoints that back the `/config` dashboard UI
(and that the policy-config page migrated onto after #602 removed the
deprecated `/api/admin/gateway/settings` endpoint).

Exercises the full round-trip through a running gateway: read default,
write a DB override, read back with source=`db`, delete the override,
read back at the original source.

Run:
    ./scripts/run_e2e.sh mock
    # or directly:
    uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/test_mock_admin_config.py -v
"""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.mock_e2e

# Picked because it defaults to False, is db_settable without restart, and is
# not set by the mock-gateway start script — so a fresh PUT will not collide
# with an ENV/CLI override (which would legitimately surface as 409).
TARGET_KEY = "dogfood_mode"


async def _get_field(client: httpx.AsyncClient, gateway_url: str, admin_headers: dict, key: str) -> dict:
    """Return the single dashboard entry for `key`, or fail the test."""
    response = await client.get(f"{gateway_url}/api/admin/config", headers=admin_headers)
    assert response.status_code == 200, f"GET /api/admin/config failed: {response.status_code} {response.text}"
    entries = response.json()["config"]
    matches = [entry for entry in entries if entry["name"] == key]
    assert len(matches) == 1, f"Expected exactly one entry for {key!r}, got {len(matches)}: {matches}"
    return matches[0]


@pytest_asyncio.fixture
async def restore_dogfood_mode(gateway_url: str, admin_headers: dict) -> AsyncIterator[None]:
    """Ensure the test starts and ends with no DB override for `dogfood_mode`.

    Tests in this module write a DB override and expect the gateway state to
    be clean on entry; this fixture cleans up even when assertions fail.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(f"{gateway_url}/api/admin/config/{TARGET_KEY}", headers=admin_headers)
    yield
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(f"{gateway_url}/api/admin/config/{TARGET_KEY}", headers=admin_headers)


@pytest.mark.asyncio
async def test_config_put_round_trips_through_gateway(
    gateway_healthy,
    gateway_url: str,
    admin_headers: dict,
    restore_dogfood_mode,
):
    """PUT a DB override, read it back, DELETE it, read the original back.

    This is the exact sequence the admin UI performs when a user toggles a
    setting in the `/config` dashboard and then clears it. Prior to #602 the
    UI hit `/api/admin/gateway/settings`; now it hits `/api/admin/config/{key}`.
    Breaking this round-trip breaks the dashboard silently, so guard it here.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        initial = await _get_field(client, gateway_url, admin_headers, TARGET_KEY)
        assert initial["value"] is False, f"Expected fresh gateway default, got {initial}"
        assert initial["db_settable"] is True
        original_source = initial["source"]

        put_response = await client.put(
            f"{gateway_url}/api/admin/config/{TARGET_KEY}",
            headers=admin_headers,
            json={"value": True},
        )
        assert put_response.status_code == 200, f"PUT failed: {put_response.status_code} {put_response.text}"
        put_body = put_response.json()
        assert put_body == {"success": True, "name": TARGET_KEY, "value": True, "source": "db"}

        after_put = await _get_field(client, gateway_url, admin_headers, TARGET_KEY)
        assert after_put["value"] is True
        assert after_put["source"] == "db"

        delete_response = await client.delete(
            f"{gateway_url}/api/admin/config/{TARGET_KEY}",
            headers=admin_headers,
        )
        assert delete_response.status_code == 200, (
            f"DELETE failed: {delete_response.status_code} {delete_response.text}"
        )
        delete_body = delete_response.json()
        assert delete_body["success"] is True
        assert delete_body["name"] == TARGET_KEY
        assert delete_body == {
            "success": True,
            "name": TARGET_KEY,
            "value": False,
            "source": original_source,
        }, f"After DELETE expected value to fall back to {original_source}; got {delete_body}"

        after_delete = await _get_field(client, gateway_url, admin_headers, TARGET_KEY)
        assert after_delete["value"] is False
        assert after_delete["source"] == original_source


@pytest.mark.asyncio
async def test_config_put_rejects_unknown_key(gateway_healthy, gateway_url: str, admin_headers: dict):
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.put(
            f"{gateway_url}/api/admin/config/definitely_not_a_real_field",
            headers=admin_headers,
            json={"value": True},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_config_put_rejects_non_db_settable(gateway_healthy, gateway_url: str, admin_headers: dict):
    """database_url is not db_settable — PUT should refuse rather than silently clobbering."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.put(
            f"{gateway_url}/api/admin/config/database_url",
            headers=admin_headers,
            json={"value": "sqlite:///tmp/nope.db"},
        )
    assert response.status_code == 400
