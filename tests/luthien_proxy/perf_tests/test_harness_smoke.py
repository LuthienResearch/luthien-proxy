"""Smoke test for the perf harness: verifies gateway starts and serves a page."""

import pytest

pytestmark = pytest.mark.perf


async def test_can_load_index(perf_gateway_url, playwright_page):
    response = await playwright_page.goto(perf_gateway_url + "/")
    assert response is not None
    assert response.status == 200
