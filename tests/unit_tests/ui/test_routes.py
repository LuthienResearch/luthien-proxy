"""Tests for UI route handlers."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from luthien_proxy.ui.routes import router

app = FastAPI()
app.include_router(router)


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestDeployInstructions:
    """Test deploy-instructions endpoint."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client: AsyncClient):
        """Deploy instructions page loads successfully."""
        response = await client.get("/deploy-instructions")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_html(self, client: AsyncClient):
        """Deploy instructions page returns HTML content."""
        response = await client.get("/deploy-instructions")
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_injects_base_url(self, client: AsyncClient):
        """Page shows the base URL derived from the request."""
        response = await client.get("/deploy-instructions")
        assert "http://test" in response.text

    @pytest.mark.asyncio
    async def test_no_hardcoded_localhost(self, client: AsyncClient):
        """Page does not contain hardcoded localhost URLs."""
        response = await client.get("/deploy-instructions")
        assert "localhost:8000" not in response.text

    @pytest.mark.asyncio
    async def test_contains_anthropic_base_url(self, client: AsyncClient):
        """Page references the env var users need to set."""
        response = await client.get("/deploy-instructions")
        assert "ANTHROPIC_BASE_URL" in response.text

    @pytest.mark.asyncio
    async def test_no_proxy_api_key_mentioned(self, client: AsyncClient):
        """Page should not reference PROXY_API_KEY (passthrough auth)."""
        response = await client.get("/deploy-instructions")
        assert "PROXY_API_KEY" not in response.text

    @pytest.mark.asyncio
    async def test_no_auth_required(self, client: AsyncClient):
        """Deploy instructions page is public (no auth redirect)."""
        response = await client.get("/deploy-instructions")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_template_placeholders_remain(self, client: AsyncClient):
        """All template placeholders should be replaced."""
        response = await client.get("/deploy-instructions")
        assert "{{BASE_URL}}" not in response.text


class TestLandingPage:
    """Test landing page includes deploy-instructions link."""

    @pytest.mark.asyncio
    async def test_landing_page_links_to_deploy_instructions(self, client: AsyncClient):
        """Landing page has a link to the deploy instructions."""
        response = await client.get("/")
        assert response.status_code == 200
        assert "/deploy-instructions" in response.text
