import pytest

from luthien_proxy.control_plane.demo_routes import DemoRequest, DemoResponse, get_demo_examples, run_live_demo


@pytest.mark.asyncio
async def test_get_demo_examples_returns_expected_sections():
    scenario = await get_demo_examples()
    assert scenario.harmful_example["policy"] == "NoOpPolicy"
    assert scenario.protected_example["policy_decision"]["verdict"] == "BLOCKED"
    assert "DROP TABLE" in scenario.harmful_example["result"]


@pytest.mark.asyncio
async def test_run_live_demo_returns_static_response_without_network():
    request = DemoRequest(prompt="Show me customer 123", mode="static")
    response = await run_live_demo(request)
    assert isinstance(response, DemoResponse)
    assert response.mode == "static"
    assert response.status == "static"
    assert response.call_id is None


@pytest.mark.asyncio
async def test_run_live_demo_handles_live_success(monkeypatch):
    captured: dict[str, object] = {}

    class StubResponse:
        status_code = 200

        def json(self):
            return {"id": "call-success"}

        def raise_for_status(self):
            return None

    class StubAsyncClient:
        def __init__(self, *_, **kwargs):
            captured["init_kwargs"] = kwargs

        async def __aenter__(self):
            captured["entered"] = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            captured["exited"] = True
            return False

        async def post(self, *args, **kwargs):
            captured["request"] = {"args": args, "kwargs": kwargs}
            return StubResponse()

    monkeypatch.setattr("httpx.AsyncClient", StubAsyncClient)

    request = DemoRequest(prompt="Show me customer 123", mode="live")
    response = await run_live_demo(request)

    assert response.call_id == "call-success"
    assert response.status == "completed"
    assert response.mode == "live"
    assert captured["entered"] is True
    assert captured["exited"] is True
    auth_header = captured["request"]["kwargs"]["headers"]["Authorization"]
    assert auth_header.startswith("Bearer ")
    payload = captured["request"]["kwargs"]["json"]
    assert payload["metadata"]["demo_request"] is True
    assert payload["messages"][0]["content"] == "Show me customer 123"


@pytest.mark.asyncio
async def test_run_live_demo_marks_blocked_on_500(monkeypatch):
    class StubResponse:
        status_code = 500

        def json(self):
            return {"id": "call-blocked", "error": {"message": "BLOCKED: policy rejected request"}}

        def raise_for_status(self):
            return None

    class StubAsyncClient:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return StubResponse()

    monkeypatch.setattr("httpx.AsyncClient", StubAsyncClient)

    request = DemoRequest(prompt="DROP TABLE customers;", mode="live")
    response = await run_live_demo(request)

    assert response.call_id == "call-blocked"
    assert response.status == "blocked"
    assert response.mode == "live"
