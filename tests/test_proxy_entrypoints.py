import sys
import types

import pytest


@pytest.mark.asyncio
async def test_proxy_module_main(monkeypatch: pytest.MonkeyPatch):
    # Fake subprocess.run used in proxy __main__
    import luthien_proxy.proxy.__main__ as m

    def fake_run(cmd, check=False, capture_output=False):
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    # Ensure local `import subprocess` pulls our fake
    monkeypatch.setitem(sys.modules, "subprocess", types.SimpleNamespace(run=fake_run))
    monkeypatch.setenv("LITELLM_CONFIG_PATH", "/tmp/config.yaml")
    monkeypatch.setenv("LITELLM_HOST", "127.0.0.1")
    monkeypatch.setenv("LITELLM_PORT", "4010")
    # Ensure main runs without raising
    m.main()


def _install_fake_litellm(monkeypatch: pytest.MonkeyPatch):
    pkg = types.ModuleType("litellm")
    pkg.callbacks = []
    subpkg = types.ModuleType("litellm.proxy.proxy_server")
    subpkg.app = object()
    # Register modules
    sys.modules["litellm"] = pkg
    sys.modules["litellm.proxy"] = types.ModuleType("litellm.proxy")
    sys.modules["litellm.proxy.proxy_server"] = subpkg


def test_start_proxy_main(monkeypatch: pytest.MonkeyPatch):
    _install_fake_litellm(monkeypatch)
    import luthien_proxy.proxy.start_proxy as sp

    run_calls = {}

    def fake_run(app, host, port, log_level, reload=False):  # noqa: ARG001
        run_calls["host"] = host
        run_calls["port"] = port
        run_calls["log_level"] = log_level

    monkeypatch.setenv("LITELLM_CONFIG_PATH", "/tmp/config.yaml")
    # Ensure `import uvicorn` resolves to our fake
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=fake_run))
    sp.main()
    assert run_calls.get("port") == 4000 or run_calls.get("port") == "4000"


@pytest.mark.asyncio
async def test_debug_callback_minimal(monkeypatch: pytest.MonkeyPatch):
    import luthien_proxy.proxy.debug_callback as dc

    class FakeResp:
        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json):  # noqa: A002
            return FakeResp()

    class AFakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json):  # noqa: A002
            return None

    # Patch httpx clients
    httpx = types.SimpleNamespace(Client=FakeClient, AsyncClient=AFakeClient)
    monkeypatch.setattr(dc, "httpx", httpx)

    cb = dc.DebugCallback()
    cb.log_pre_api_call(None, None, {"k": 1})
    await cb.async_log_pre_api_call(None, None, {"k": 1})
    await cb.async_on_stream_event({}, {"a": 1}, 0, 0)
    await cb.async_post_call_success_hook({"x": 1}, None, {"choices": []})
    await cb.async_post_call_failure_hook({"x": 1}, Exception("err"), None)

    async def agen():
        yield {"choices": []}

    # iterator hook yields through
    out = []
    async for item in cb.async_post_call_streaming_iterator_hook(None, agen(), {}):
        out.append(item)
    assert out
