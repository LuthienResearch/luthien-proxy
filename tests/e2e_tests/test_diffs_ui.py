"""E2E tests for the diff viewer UI.

These tests verify that:
- The /diffs route is accessible and returns HTML
- The page loads without errors and contains expected content

Prerequisites:
- Gateway must be running (docker compose up gateway)
- Valid admin API credentials in env or .env
"""

import pytest
from tests.e2e_tests.conftest import ADMIN_API_KEY, GATEWAY_URL


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_diffs_page_accessible(http_client, gateway_healthy):
    """Verify the diff viewer page is accessible at /diffs and returns HTML."""
    response = await http_client.get(
        f"{GATEWAY_URL}/diffs",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Diff Viewer" in response.text or "diff-viewer" in response.text


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_diffs_page_requires_auth(http_client, gateway_healthy):
    """Verify the diff viewer page requires authentication."""
    # Request without auth should redirect to login
    response = await http_client.get(f"{GATEWAY_URL}/diffs", follow_redirects=False)

    # Should either redirect (302) or return unauthorized (401)
    assert response.status_code in [302, 401], f"Expected redirect or unauthorized, got {response.status_code}"

    if response.status_code == 302:
        location = response.headers.get("location", "")
        assert "/login" in location, f"Expected redirect to login page, got: {location}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_diffs_page_has_navigation(http_client, gateway_healthy):
    """Verify the diff viewer page includes the shared navigation component."""
    response = await http_client.get(
        f"{GATEWAY_URL}/diffs",
        headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    )

    assert response.status_code == 200
    html_content = response.text

    # Check for navigation elements (from nav.js/nav.css)
    nav_indicators = [
        "luthien-nav",  # CSS class
        "nav.js",  # Script include
        "Activity",  # Nav link text
        "History",  # Nav link text
        "Policies",  # Nav link text
    ]

    # At least some navigation indicators should be present
    nav_found = sum(1 for indicator in nav_indicators if indicator in html_content)
    assert nav_found >= 2, (
        f"Expected navigation elements in HTML. Found {nav_found} indicators in: {html_content[:500]}..."
    )
