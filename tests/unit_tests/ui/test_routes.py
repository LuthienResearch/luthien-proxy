# ABOUTME: Unit tests for UI routes
# ABOUTME: Tests that UI route handlers return correct responses

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
    async def test_contains_key_content(self, client: AsyncClient):
        """Deploy instructions page includes essential setup information."""
        response = await client.get("/deploy-instructions")
        body = response.text
        assert "quick_start.sh" in body
        assert "ANTHROPIC_BASE_URL" in body
        assert "PROXY_API_KEY" in body
        assert "launch_claude_code.sh" in body

    @pytest.mark.asyncio
    async def test_no_auth_required(self, client: AsyncClient):
        """Deploy instructions page is public (no auth redirect)."""
        response = await client.get("/deploy-instructions")
        # Should not redirect to login
        assert response.status_code == 200


class TestLandingPage:
    """Test landing page includes deploy-instructions link."""

    @pytest.mark.asyncio
    async def test_landing_page_links_to_deploy_instructions(self, client: AsyncClient):
        """Landing page has a link to the deploy instructions."""
        response = await client.get("/")
        assert response.status_code == 200
        assert "/deploy-instructions" in response.text
