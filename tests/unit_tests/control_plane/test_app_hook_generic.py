import asyncio
from collections import Counter
from typing import Any

import pytest

import luthien_proxy.control_plane.app as app_mod
from luthien_proxy.policies.noop import NoOpPolicy


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, debug_type: str, payload: dict[str, Any], *_args, **_kwargs) -> None:
        self.calls.append((debug_type, payload))


@pytest.mark.asyncio
async def test_hook_generic_returns_payload_when_no_handler():
    recorder = _Recorder()
    counters: Counter[str] = Counter()
    payload = {"foo": "bar", "post_time_ns": 123}

    out = await app_mod.hook_generic(
        "unknown_hook",
        payload,
        debug_writer=recorder,
        policy=NoOpPolicy(),
        counters=counters,
    )

    assert out == {"foo": "bar"}  # post_time_ns removed
    await asyncio.sleep(0)
    assert recorder.calls
    assert counters["unknown_hook"] == 1


class DummyPolicy:
    async def testhook(self, **kwargs):
        return {"ok": True, **kwargs}


@pytest.mark.asyncio
async def test_hook_generic_calls_active_policy():
    recorder = _Recorder()
    counters: Counter[str] = Counter()

    out = await app_mod.hook_generic(
        "testhook",
        {"x": 1},
        debug_writer=recorder,
        policy=DummyPolicy(),
        counters=counters,
    )

    assert out.get("ok") is True and out.get("x") == 1
    await asyncio.sleep(0)
    assert recorder.calls
    assert counters["testhook"] == 1
