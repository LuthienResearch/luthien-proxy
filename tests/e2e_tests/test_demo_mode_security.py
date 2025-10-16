"""E2E tests verifying demo endpoints are properly gated by ENABLE_DEMO_MODE flag."""

import httpx
import pytest
from tests.e2e_tests.helpers import E2ESettings


@pytest.fixture(scope="module")
async def ensure_stack_ready(e2e_settings: E2ESettings):
    """Ensure services are running before tests."""
    from tests.e2e_tests.helpers import ensure_services_available

    await ensure_services_available(e2e_settings)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_demo_ui_returns_404_when_demo_mode_disabled(ensure_stack_ready):
    """Verify /ui/demo returns 404 when ENABLE_DEMO_MODE is not set (default)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get("http://localhost:8081/ui/demo")
        assert response.status_code == 404, (
            "Demo UI endpoint should return 404 when ENABLE_DEMO_MODE is not enabled. "
            "This is a security feature to prevent demo endpoints from being exposed in production."
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_demo_examples_returns_404_when_demo_mode_disabled(ensure_stack_ready):
    """Verify /demo/examples returns 404 when ENABLE_DEMO_MODE is not set (default)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get("http://localhost:8081/demo/examples")
        assert response.status_code == 404, (
            "Demo examples endpoint should return 404 when ENABLE_DEMO_MODE is not enabled."
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_demo_run_returns_404_when_demo_mode_disabled(ensure_stack_ready):
    """Verify /demo/run returns 404 when ENABLE_DEMO_MODE is not set (default)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "http://localhost:8081/demo/run",
            json={"prompt": "test", "mode": "static"},
        )
        assert response.status_code == 404, "Demo run endpoint should return 404 when ENABLE_DEMO_MODE is not enabled."
