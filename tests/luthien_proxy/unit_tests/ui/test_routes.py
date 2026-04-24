"""Tests for UI route handlers."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from luthien_proxy.dependencies import get_admin_key, get_api_key
from luthien_proxy.ui.routes import router

app = FastAPI()
app.include_router(router)
# With no admin key configured, check_auth_or_redirect treats requests as
# authenticated — matches the dockerless/unconfigured dev default.
app.dependency_overrides[get_admin_key] = lambda: None
app.dependency_overrides[get_api_key] = lambda: None


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
    async def test_no_client_api_key_mentioned(self, client: AsyncClient):
        """Page should not reference CLIENT_API_KEY (passthrough auth)."""
        response = await client.get("/client-setup")
        assert "CLIENT_API_KEY" not in response.text

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


class TestCredentialsPage:
    """Test the /credentials UI route."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client: AsyncClient):
        """Credentials page loads successfully when auth is not configured."""
        response = await client.get("/credentials")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_html(self, client: AsyncClient):
        """Credentials page returns HTML content."""
        response = await client.get("/credentials")
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_contains_server_credentials_section(self, client: AsyncClient):
        """Server-credentials CRUD UI is present on the page."""
        response = await client.get("/credentials")
        # The section the new UI introduces — guards against a future refactor
        # that removes the server-credential CRUD without a nav/route update.
        assert "server-credentials-section" in response.text
        assert "server-cred-form" in response.text


class TestNavCredentialsLink:
    """Nav entry for /credentials must exist so the page is reachable."""

    def test_nav_js_lists_credentials_link(self):
        nav_js = Path(__file__).resolve().parents[4] / "src" / "luthien_proxy" / "static" / "nav.js"
        content = nav_js.read_text()
        # Belt-and-suspenders: future refactors that rebuild the link list
        # should preserve the Credentials entry.
        assert "'/credentials'" in content
        assert "'Credentials'" in content
