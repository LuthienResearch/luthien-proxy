import json
from contextlib import asynccontextmanager
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


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn
        self.calls = 0

    @asynccontextmanager
    async def connection(self):
        self.calls += 1
        yield self._conn


@pytest.mark.asyncio
async def test_get_debug_entries_parses_blob():
    rows = [
        {
            "id": 1,
            "time_created": datetime.now(tz=timezone.utc),
            "debug_type_identifier": "t1",
            "jsonblob": json.dumps({"a": 1}),
        },
        {
            "id": 2,
            "time_created": datetime.now(tz=timezone.utc),
            "debug_type_identifier": "t1",
            "jsonblob": json.dumps({"b": 2}),
        },
    ]
    conn = FakeConn(rows=rows)

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})
    pool = FakePool(conn)

    out = await app_mod.get_debug_entries("t1", pool=pool, config=config)
    assert len(out) == 2
    assert out[0].jsonblob.get("a") == 1
    assert out[1].jsonblob.get("b") == 2


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

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})
    pool = FakePool(conn)

    out = await app_mod.get_debug_types(pool=pool, config=config)
    assert [r.debug_type_identifier for r in out] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_get_debug_page():
    rows = [
        {
            "id": 1,
            "time_created": datetime.now(tz=timezone.utc),
            "debug_type_identifier": "t1",
            "jsonblob": json.dumps({"a": 1}),
        },
    ]
    conn = FakeConn(rows=rows, row={"cnt": 10})

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})
    pool = FakePool(conn)

    out = await app_mod.get_debug_page("t1", page=2, page_size=1, pool=pool, config=config)
    assert out.total == 10 and len(out.items) == 1 and out.page == 2


@pytest.mark.asyncio
async def test_recent_call_ids():
    rows = [
        {"call_id": "A", "event_count": 2, "latest": datetime.now(tz=timezone.utc)},
        {"call_id": "B", "event_count": 1, "latest": datetime.now(tz=timezone.utc)},
    ]
    conn = FakeConn(rows=rows)

    config = ProjectConfig(env_map={"DATABASE_URL": "postgres://example"})
    pool = FakePool(conn)

    out = await app_mod.recent_call_ids(limit=2, pool=pool, config=config)
    assert [r.call_id for r in out] == ["A", "B"]
