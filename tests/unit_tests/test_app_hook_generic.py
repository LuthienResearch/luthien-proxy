import asyncio

import pytest

import luthien_proxy.control_plane.app as app_mod


@pytest.mark.asyncio
async def test_hook_generic_returns_payload_when_no_handler(
    monkeypatch: pytest.MonkeyPatch,
):
    # Avoid DB side effects
    monkeypatch.setattr(app_mod, "_insert_debug", lambda *a, **k: asyncio.sleep(0))
    app_mod.active_policy = None
    payload = {"foo": "bar", "post_time_ns": 123}
    out = await app_mod.hook_generic("unknown_hook", payload)
    assert out == {"foo": "bar"}  # post_time_ns removed


class DummyPolicy:
    async def testhook(self, **kwargs):
        return {"ok": True, **kwargs}


@pytest.mark.asyncio
async def test_hook_generic_calls_active_policy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app_mod, "_insert_debug", lambda *a, **k: asyncio.sleep(0))
    app_mod.active_policy = DummyPolicy()
    out = await app_mod.hook_generic("testhook", {"x": 1})
    assert out.get("ok") is True and out.get("x") == 1
