import pytest
from starlette.requests import Request

from luthien_proxy.control_plane.ui import (
    conversation_by_call_ui,
    conversation_monitor_ui,
    debug_browser,
    debug_ui,
    hooks_conversation_ui,
)


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
    assert (await hooks_conversation_ui(req)).status_code == 200
    assert (await conversation_by_call_ui(req)).status_code == 200
    assert (await conversation_monitor_ui(req)).status_code == 200
