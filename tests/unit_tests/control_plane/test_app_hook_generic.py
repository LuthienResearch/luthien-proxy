import asyncio

import pytest

import luthien_proxy.control_plane.app as app_mod


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, debug_type: str, payload: dict[str, object], *_args, **_kwargs) -> None:
        self.calls.append((debug_type, payload))


@pytest.mark.asyncio
async def test_hook_generic_returns_payload_when_no_handler():
    recorder = _Recorder()
    original_writer = app_mod.debug_log_writer
    original_policy = app_mod.active_policy
    try:
        app_mod.debug_log_writer = recorder  # type: ignore[assignment]
        app_mod.active_policy = None
        payload = {"foo": "bar", "post_time_ns": 123}
        out = await app_mod.hook_generic("unknown_hook", payload)
        assert out == {"foo": "bar"}  # post_time_ns removed
        await asyncio.sleep(0)
        assert recorder.calls
    finally:
        app_mod.debug_log_writer = original_writer
        app_mod.active_policy = original_policy


class DummyPolicy:
    async def testhook(self, **kwargs):
        return {"ok": True, **kwargs}


@pytest.mark.asyncio
async def test_hook_generic_calls_active_policy():
    recorder = _Recorder()
    original_writer = app_mod.debug_log_writer
    original_policy = app_mod.active_policy
    try:
        app_mod.debug_log_writer = recorder  # type: ignore[assignment]
        app_mod.active_policy = DummyPolicy()
        out = await app_mod.hook_generic("testhook", {"x": 1})
        assert out.get("ok") is True and out.get("x") == 1
        await asyncio.sleep(0)
        assert recorder.calls
    finally:
        app_mod.debug_log_writer = original_writer
        app_mod.active_policy = original_policy
