import json
from datetime import datetime, timezone

import pytest

import luthien_proxy.control_plane.app as app_mod
from luthien_proxy.utils.project_config import ProjectConfig


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


@pytest.mark.asyncio
async def test_get_debug_entries_parses_blob():
    # One row with dict jsonblob, one with string jsonblob
    rows = [
        {
            "id": 1,
            "time_created": datetime.now(tz=timezone.utc),
            "debug_type_identifier": "t1",
            "jsonblob": {"a": 1},
        },
        {
            "id": 2,
            "time_created": datetime.now(tz=timezone.utc),
            "debug_type_identifier": "t1",
            "jsonblob": json.dumps({"b": 2}),
        },
    ]
    conn = FakeConn(rows=rows)

    async def fake_connect(_):
        return conn

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})

    out = await app_mod.get_debug_entries("t1", connect=fake_connect, config=config)
    assert len(out) == 2 and out[0].jsonblob.get("a") == 1 and out[1].jsonblob.get("b") == 2


@pytest.mark.asyncio
async def test_get_debug_types():
    rows = [
        {
            "debug_type_identifier": "t1",
            "count": 3,
            "latest": datetime.now(tz=timezone.utc),
        },
        {
            "debug_type_identifier": "t2",
            "count": 1,
            "latest": datetime.now(tz=timezone.utc),
        },
    ]
    conn = FakeConn(rows=rows)

    async def fake_connect(_):
        return conn

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})

    out = await app_mod.get_debug_types(connect=fake_connect, config=config)
    assert [r.debug_type_identifier for r in out] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_get_debug_page():
    rows = [
        {
            "id": 1,
            "time_created": datetime.now(tz=timezone.utc),
            "debug_type_identifier": "t1",
            "jsonblob": {"a": 1},
        },
    ]
    conn = FakeConn(rows=rows, row={"cnt": 10})

    async def fake_connect(_):
        return conn

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})

    out = await app_mod.get_debug_page("t1", page=2, page_size=1, connect=fake_connect, config=config)
    assert out.total == 10 and len(out.items) == 1 and out.page == 2


@pytest.mark.asyncio
async def test_trace_by_call_id_sorts_by_ns():
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
    conn = FakeConn(rows=rows)

    async def fake_connect(_):
        return conn

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})

    out = await app_mod.trace_by_call_id(call_id, connect=fake_connect, config=config)
    assert [e.hook for e in out.entries] == ["y", "x"]


@pytest.mark.asyncio
async def test_recent_call_ids():
    rows = [
        {"cid": "A", "cnt": 2, "latest": datetime.now(tz=timezone.utc)},
        {"cid": "B", "cnt": 1, "latest": datetime.now(tz=timezone.utc)},
    ]
    conn = FakeConn(rows=rows)

    async def fake_connect(_):
        return conn

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})

    out = await app_mod.recent_call_ids(limit=2, connect=fake_connect, config=config)
    assert [r.call_id for r in out] == ["A", "B"]
