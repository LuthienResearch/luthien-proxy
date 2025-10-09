import json
from datetime import UTC, datetime

import pytest

from luthien_proxy.control_plane.debug_routes import (
    DebugPage,
    DebugTypeInfo,
    ToolCallLogEntry,
    get_conversation_logs,
    get_debug_entries,
    get_debug_page,
    get_debug_types,
    get_tool_call_logs,
)
from luthien_proxy.utils.project_config import ProjectConfig


class _FakeDebugConn:
    def __init__(self, fetch_rows, fetchrow_result=None):
        self._fetch_rows = fetch_rows
        self._fetchrow_result = fetchrow_result

    async def fetch(self, query, *params):
        if isinstance(query, str) and "FROM conversation_tool_calls" in query:
            rows = self._fetch_rows
            if len(params) >= 2 and isinstance(params[0], str):
                rows = [row for row in rows if row.get("call_id") == params[0]]
            limit = params[-1] if params else None
            if isinstance(limit, int):
                return rows[:limit]
            return rows
        return self._fetch_rows

    async def fetchrow(self, *args, **kwargs):
        return self._fetchrow_result


class _FakePool:
    def __init__(self, conn: _FakeDebugConn):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


@pytest.fixture()
def project_config() -> ProjectConfig:
    env = {
        "LITELLM_CONFIG_PATH": "config.yaml",
        "REDIS_URL": "redis://localhost:6379/0",
        "LUTHIEN_POLICY_CONFIG": "policy.yaml",
        "DATABASE_URL": "postgres://user:pass@localhost:5432/db",
    }
    return ProjectConfig(env_map=env)


@pytest.mark.asyncio
async def test_get_debug_entries_parses_json(project_config: ProjectConfig):
    now = datetime.now(UTC)
    conn = _FakeDebugConn(
        [
            {
                "id": 1,
                "time_created": now,
                "debug_type_identifier": "hook:test",
                "jsonblob": '{"foo": 1}',
            }
        ]
    )
    pool = _FakePool(conn)

    entries = await get_debug_entries("hook:test", pool=pool, config=project_config)

    assert len(entries) == 1
    assert entries[0].id == "1"
    assert entries[0].jsonblob == {"foo": 1}
    assert entries[0].debug_type_identifier == "hook:test"


@pytest.mark.asyncio
async def test_get_debug_entries_handles_raw_blob(project_config: ProjectConfig):
    now = datetime.now(UTC)
    conn = _FakeDebugConn(
        [
            {
                "id": 1,
                "time_created": now,
                "debug_type_identifier": "hook:test",
                "jsonblob": "{",
            }
        ]
    )
    pool = _FakePool(conn)

    entries = await get_debug_entries("hook:test", pool=pool, config=project_config)

    assert entries[0].jsonblob["raw"] == "{"
    assert "error" in entries[0].jsonblob


@pytest.mark.asyncio
async def test_get_debug_entries_without_database_returns_empty(project_config: ProjectConfig):
    entries = await get_debug_entries("hook:test", pool=None, config=project_config)
    assert entries == []


@pytest.mark.asyncio
async def test_get_debug_types_returns_summary(project_config: ProjectConfig):
    now = datetime.now(UTC)
    conn = _FakeDebugConn(
        [
            {
                "debug_type_identifier": "hook:test",
                "count": 3,
                "latest": now,
            }
        ]
    )
    pool = _FakePool(conn)

    types = await get_debug_types(pool=pool, config=project_config)

    assert types == [DebugTypeInfo(debug_type_identifier="hook:test", count=3, latest=now)]


@pytest.mark.asyncio
async def test_get_debug_page_handles_raw_strings(project_config: ProjectConfig):
    now = datetime.now(UTC)
    conn = _FakeDebugConn(
        fetch_rows=[
            {
                "id": 2,
                "time_created": now,
                "debug_type_identifier": "hook:test",
                "jsonblob": "not-json",
            }
        ],
        fetchrow_result={"cnt": 5},
    )
    pool = _FakePool(conn)

    page = await get_debug_page(
        "hook:test",
        page=1,
        page_size=20,
        pool=pool,
        config=project_config,
    )

    assert isinstance(page, DebugPage)
    assert page.total == 5
    blob = page.items[0].jsonblob
    assert blob["raw"] == "not-json"
    assert "error" in blob


@pytest.mark.asyncio
async def test_get_debug_types_without_database_returns_empty(project_config: ProjectConfig):
    types = await get_debug_types(pool=None, config=project_config)
    assert types == []


class _ErrorConn:
    async def fetch(self, *args, **kwargs):
        raise RuntimeError("boom")

    async def fetchrow(self, *args, **kwargs):
        raise RuntimeError("boom")


class _ErrorPool:
    def connection(self):
        conn = _ErrorConn()

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_get_debug_types_logs_and_recovers(project_config: ProjectConfig):
    pool = _ErrorPool()
    types = await get_debug_types(pool=pool, config=project_config)
    assert types == []


@pytest.mark.asyncio
async def test_get_debug_page_logs_and_recovers(project_config: ProjectConfig):
    pool = _ErrorPool()

    page = await get_debug_page(
        "hook:test",
        page=1,
        page_size=20,
        pool=pool,
        config=project_config,
    )

    assert isinstance(page, DebugPage)
    assert page.items == []
    assert page.total == 0


@pytest.mark.asyncio
async def test_get_conversation_logs_filters_call_id(project_config: ProjectConfig):
    now = datetime.now(UTC)
    payload_match = {
        "call_id": "call-1",
        "timestamp": now.isoformat(),
        "direction": "request",
    }
    payload_other = {
        "call_id": "call-2",
        "timestamp": now.isoformat(),
        "direction": "response",
    }
    conn = _FakeDebugConn(
        [
            {
                "id": 1,
                "time_created": now,
                "debug_type_identifier": "conversation:turn",
                "jsonblob": json.dumps(payload_match),
            },
            {
                "id": 2,
                "time_created": now,
                "debug_type_identifier": "conversation:turn",
                "jsonblob": json.dumps(payload_other),
            },
        ]
    )
    pool = _FakePool(conn)

    logs = await get_conversation_logs(call_id="call-1", limit=10, pool=pool, config=project_config)

    assert len(logs) == 1
    entry = logs[0]
    assert entry.call_id == "call-1"
    assert entry.direction == "request"
    assert entry.timestamp == datetime.fromisoformat(payload_match["timestamp"])


@pytest.mark.asyncio
async def test_get_conversation_logs_handles_invalid_timestamp(project_config: ProjectConfig):
    now = datetime.now(UTC)
    payload = {
        "call_id": "call-3",
        "timestamp": "not-a-timestamp",
        "direction": "response",
    }
    conn = _FakeDebugConn(
        [
            {
                "id": 3,
                "time_created": now,
                "debug_type_identifier": "conversation:turn",
                "jsonblob": json.dumps(payload),
            }
        ]
    )
    pool = _FakePool(conn)

    logs = await get_conversation_logs(pool=pool, config=project_config)

    assert len(logs) == 1
    entry = logs[0]
    assert entry.call_id == "call-3"
    assert entry.timestamp == now


@pytest.mark.asyncio
async def test_get_tool_call_logs_parses_entries(project_config: ProjectConfig):
    now = datetime.now(UTC)
    conn = _FakeDebugConn(
        [
            {
                "call_id": "call-1",
                "trace_id": "trace-1",
                "tool_call_id": "stream-1",
                "name": "shell",
                "arguments": "{}",
                "status": "emitted",
                "response": None,
                "chunks_buffered": 3,
                "created_at": now,
            }
        ]
    )
    pool = _FakePool(conn)

    logs = await get_tool_call_logs(pool=pool, config=project_config)

    assert len(logs) == 1
    entry = logs[0]
    assert isinstance(entry, ToolCallLogEntry)
    assert entry.call_id == "call-1"
    assert entry.trace_id == "trace-1"
    assert entry.stream_id == "stream-1"
    assert entry.chunks_buffered == 3
    assert entry.tool_calls[0]["name"] == "shell"


@pytest.mark.asyncio
async def test_get_tool_call_logs_filters_call_id(project_config: ProjectConfig):
    now = datetime.now(UTC)
    conn = _FakeDebugConn(
        [
            {
                "call_id": "other",
                "trace_id": "trace-1",
                "tool_call_id": "stream-1",
                "name": "shell",
                "arguments": "{}",
                "status": "emitted",
                "response": None,
                "chunks_buffered": 1,
                "created_at": now,
            }
        ]
    )
    pool = _FakePool(conn)

    logs = await get_tool_call_logs(call_id="call-1", pool=pool, config=project_config)

    assert logs == []
