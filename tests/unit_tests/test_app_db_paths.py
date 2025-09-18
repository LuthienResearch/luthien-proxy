import json
from datetime import datetime

import pytest

import luthien_proxy.control_plane.app as app_mod


class FakeConn:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row
        self.executed = []

    async def execute(self, sql: str, *params):
        self.executed.append((sql, params))
        return "OK"

    async def fetch(self, sql: str, *params):
        return list(self._rows)

    async def fetchrow(self, sql: str, *params):
        return self._row

    async def close(self):
        return None


def _patch_connect(monkeypatch: pytest.MonkeyPatch, rows=None, row=None):
    async def _connect(_):
        return FakeConn(rows=rows, row=row)

    monkeypatch.setattr(app_mod.asyncpg, "connect", _connect)


@pytest.mark.asyncio
async def test_get_debug_entries_parses_blob(monkeypatch: pytest.MonkeyPatch):
    # One row with dict jsonblob, one with string jsonblob
    rows = [
        {
            "id": 1,
            "time_created": datetime.utcnow(),
            "debug_type_identifier": "t1",
            "jsonblob": {"a": 1},
        },
        {
            "id": 2,
            "time_created": datetime.utcnow(),
            "debug_type_identifier": "t1",
            "jsonblob": json.dumps({"b": 2}),
        },
    ]
    _patch_connect(monkeypatch, rows=rows)
    out = await app_mod.get_debug_entries("t1")
    assert len(out) == 2 and out[0].jsonblob.get("a") == 1 and out[1].jsonblob.get("b") == 2


@pytest.mark.asyncio
async def test_get_debug_types(monkeypatch: pytest.MonkeyPatch):
    rows = [
        {"debug_type_identifier": "t1", "count": 3, "latest": datetime.utcnow()},
        {"debug_type_identifier": "t2", "count": 1, "latest": datetime.utcnow()},
    ]
    _patch_connect(monkeypatch, rows=rows)
    out = await app_mod.get_debug_types()
    assert [r.debug_type_identifier for r in out] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_get_debug_page(monkeypatch: pytest.MonkeyPatch):
    rows = [
        {
            "id": 1,
            "time_created": datetime.utcnow(),
            "debug_type_identifier": "t1",
            "jsonblob": {"a": 1},
        },
    ]
    _patch_connect(monkeypatch, rows=rows, row={"cnt": 10})
    out = await app_mod.get_debug_page("t1", page=2, page_size=1)
    assert out.total == 10 and len(out.items) == 1 and out.page == 2


@pytest.mark.asyncio
async def test_trace_by_call_id_sorts_by_ns(monkeypatch: pytest.MonkeyPatch):
    call_id = "C"
    rows = [
        {
            "time_created": datetime.fromtimestamp(100),
            "debug_type_identifier": "hook:x",
            "jsonblob": {"payload": {"post_time_ns": 1000}, "hook": "x"},
        },
        {
            "time_created": datetime.fromtimestamp(50),
            "debug_type_identifier": "hook:y",
            "jsonblob": {"payload": {"post_time_ns": 500}, "hook": "y"},
        },
    ]
    _patch_connect(monkeypatch, rows=rows)
    out = await app_mod.trace_by_call_id(call_id)
    assert [e.hook for e in out.entries] == ["y", "x"]


@pytest.mark.asyncio
async def test_recent_call_ids(monkeypatch: pytest.MonkeyPatch):
    rows = [
        {"cid": "A", "cnt": 2, "latest": datetime.utcnow()},
        {"cid": "B", "cnt": 1, "latest": datetime.utcnow()},
    ]
    _patch_connect(monkeypatch, rows=rows)
    out = await app_mod.recent_call_ids(limit=2)
    assert [r.call_id for r in out] == ["A", "B"]
