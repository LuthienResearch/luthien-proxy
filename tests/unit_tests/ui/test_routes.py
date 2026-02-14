"""Tests for UI route handlers."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from luthien_proxy.settings import Settings
from luthien_proxy.ui.routes import router

app = FastAPI()
app.include_router(router)


def _make_settings(**overrides: object) -> Settings:
    defaults = {
        "proxy_api_key": "test-proxy-key-123",
        "admin_api_key": None,
        "database_url": "",
        "redis_url": "redis://localhost:6379",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestDeployInstructions:
    """Test deploy-instructions endpoint."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client: AsyncClient):
        """Deploy instructions page loads successfully."""
        with patch("luthien_proxy.ui.routes.get_settings", return_value=_make_settings()):
            response = await client.get("/deploy-instructions")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_html(self, client: AsyncClient):
        """Deploy instructions page returns HTML content."""
        with patch("luthien_proxy.ui.routes.get_settings", return_value=_make_settings()):
            response = await client.get("/deploy-instructions")
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_injects_proxy_api_key(self, client: AsyncClient):
        """Page shows the actual PROXY_API_KEY value."""
        with patch(
            "luthien_proxy.ui.routes.get_settings",
            return_value=_make_settings(proxy_api_key="sk-my-secret-key"),
        ):
            response = await client.get("/deploy-instructions")
        assert "sk-my-secret-key" in response.text

    @pytest.mark.asyncio
    async def test_injects_base_url(self, client: AsyncClient):
        """Page shows the base URL derived from the request."""
        with patch("luthien_proxy.ui.routes.get_settings", return_value=_make_settings()):
            response = await client.get("/deploy-instructions")
        assert "http://test" in response.text

    @pytest.mark.asyncio
    async def test_no_hardcoded_localhost(self, client: AsyncClient):
        """Page does not contain hardcoded localhost URLs."""
        with patch("luthien_proxy.ui.routes.get_settings", return_value=_make_settings()):
            response = await client.get("/deploy-instructions")
        assert "localhost:8000" not in response.text

    @pytest.mark.asyncio
    async def test_contains_essential_env_vars(self, client: AsyncClient):
        """Page references the env vars users need to set."""
        with patch("luthien_proxy.ui.routes.get_settings", return_value=_make_settings()):
            response = await client.get("/deploy-instructions")
        body = response.text
        assert "ANTHROPIC_BASE_URL" in body
        assert "ANTHROPIC_API_KEY" in body

    @pytest.mark.asyncio
    async def test_no_auth_required(self, client: AsyncClient):
        """Deploy instructions page is public (no auth redirect)."""
        with patch("luthien_proxy.ui.routes.get_settings", return_value=_make_settings()):
            response = await client.get("/deploy-instructions")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_proxy_key_shows_placeholder(self, client: AsyncClient):
        """When PROXY_API_KEY is not set, page shows a clear placeholder."""
        with patch(
            "luthien_proxy.ui.routes.get_settings",
            return_value=_make_settings(proxy_api_key=None),
        ):
            response = await client.get("/deploy-instructions")
        assert "(not configured)" in response.text


class TestLandingPage:
    """Test landing page includes deploy-instructions link."""

    @pytest.mark.asyncio
    async def test_landing_page_links_to_deploy_instructions(self, client: AsyncClient):
        """Landing page has a link to the deploy instructions."""
        response = await client.get("/")
        assert response.status_code == 200
        assert "/deploy-instructions" in response.text
