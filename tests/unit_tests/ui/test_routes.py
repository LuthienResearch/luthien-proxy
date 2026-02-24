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


class TestClientSetup:
    """Test client-setup endpoint."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client: AsyncClient):
        """Client setup page loads successfully."""
        response = await client.get("/client-setup")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_html(self, client: AsyncClient):
        """Client setup page returns HTML content."""
        response = await client.get("/client-setup")
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_injects_base_url(self, client: AsyncClient):
        """Page shows the base URL derived from the request."""
        response = await client.get("/client-setup")
        assert "http://test" in response.text

    @pytest.mark.asyncio
    async def test_no_hardcoded_localhost(self, client: AsyncClient):
        """Page does not contain hardcoded localhost URLs."""
        response = await client.get("/client-setup")
        assert "localhost:8000" not in response.text

    @pytest.mark.asyncio
    async def test_contains_anthropic_base_url(self, client: AsyncClient):
        """Page references the env var users need to set."""
        response = await client.get("/client-setup")
        assert "ANTHROPIC_BASE_URL" in response.text

    @pytest.mark.asyncio
    async def test_no_proxy_api_key_mentioned(self, client: AsyncClient):
        """Page should not reference PROXY_API_KEY (passthrough auth)."""
        response = await client.get("/client-setup")
        assert "PROXY_API_KEY" not in response.text

    @pytest.mark.asyncio
    async def test_no_template_placeholders_remain(self, client: AsyncClient):
        """All template placeholders should be replaced."""
        response = await client.get("/client-setup")
        assert "{{BASE_URL}}" not in response.text

    @pytest.mark.asyncio
    async def test_base_url_is_html_escaped(self, client: AsyncClient):
        """HTML-special characters in the host header are escaped."""
        response = await client.get(
            "/client-setup",
            headers={"host": 'example.com"><script>alert(1)</script>'},
        )
        assert response.status_code == 200
        body = response.text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body or "&#x27;" in body

    @pytest.mark.asyncio
    async def test_x_forwarded_proto_https(self, client: AsyncClient):
        """X-Forwarded-Proto: https upgrades the base URL scheme."""
        response = await client.get(
            "/client-setup",
            headers={"x-forwarded-proto": "https"},
        )
        assert response.status_code == 200
        assert "https://test" in response.text


class TestLandingPage:
    """Test landing page includes client-setup link."""

    @pytest.mark.asyncio
    async def test_landing_page_links_to_client_setup(self, client: AsyncClient):
        """Landing page has a link to the client setup page."""
        response = await client.get("/")
        assert response.status_code == 200
        assert "/client-setup" in response.text
