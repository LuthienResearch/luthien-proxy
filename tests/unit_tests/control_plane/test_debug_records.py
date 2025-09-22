import json

import pytest

from luthien_proxy.control_plane.debug_records import record_debug_event


class _FakeConn:
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    async def execute(self, query: str, debug_type: str, payload_json: str) -> None:
        self.calls.append((query, debug_type, payload_json))


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_record_debug_event_writes_payload():
    conn = _FakeConn()
    pool = _FakePool(conn)
    payload = {"foo": "bar"}

    await record_debug_event(pool, "hook:test", payload)

    assert conn.calls
    _, debug_type, payload_json = conn.calls[0]
    assert debug_type == "hook:test"
    assert json.loads(payload_json) == payload


@pytest.mark.asyncio
async def test_record_debug_event_skips_when_pool_missing():
    # Should not raise when database pool is not configured
    await record_debug_event(None, "hook:test", {"foo": "bar"})
