import pytest
from starlette.requests import Request

from luthien_proxy.control_plane.ui import debug_browser, debug_ui, hooks_trace_ui


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/",
            "headers": [],
        }
    )


@pytest.mark.asyncio
async def test_ui_routes_return_template_response():
    req = make_request()
    assert (await debug_browser(req)).status_code == 200
    assert (await debug_ui(req, "X")).status_code == 200
    assert (await hooks_trace_ui(req)).status_code == 200
