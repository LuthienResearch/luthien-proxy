import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from luthien_proxy.control_plane.hooks_routes import (
    CallIdInfo,
    TraceResponse,
    get_hook_counters,
    hook_generic,
    recent_call_ids,
    trace_by_call_id,
)
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.utils.project_config import ProjectConfig


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):
        return self._rows

    async def fetchrow(self, *args, **kwargs):
        raise AssertionError("fetchrow should not be called in this test")


class _FakeTraceConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):
        return self._rows

    async def fetchrow(self, *args, **kwargs):
        return None


class _PoolWrapper:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


class _TestPolicy(LuthienPolicy):
    def __init__(self):
        self.calls = []

    async def async_post_call_success_hook(self, **payload):
        self.calls.append(payload)
        return {"handled": payload}


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
async def test_hook_generic_invokes_policy_and_records_debug():
    policy = _TestPolicy()
    counters = Counter()
    debug_records = []

    async def _debug_writer(debug_type, record):
        debug_records.append((debug_type, record))

    payload = {
        "data": {"litellm_call_id": "abc123"},
        "post_time_ns": 5,
        "value": 42,
    }

    response = await hook_generic(
        "async_post_call_success_hook",
        payload,
        debug_writer=_debug_writer,
        policy=policy,
        counters=counters,
    )

    await asyncio.sleep(0)

    assert response == {"handled": {"data": {"litellm_call_id": "abc123"}, "value": 42}}
    assert counters["async_post_call_success_hook"] == 1
    assert policy.calls and policy.calls[0] == {"data": {"litellm_call_id": "abc123"}, "value": 42}
    assert debug_records[0][0] == "hook:async_post_call_success_hook"
    assert debug_records[0][1]["litellm_call_id"] == "abc123"


@pytest.mark.asyncio
async def test_hook_generic_returns_payload_when_handler_missing():
    counters = Counter()

    async def _debug_writer(*_, **__):
        return None

    payload = {"value": 1, "post_time_ns": 7}
    result = await hook_generic(
        "unknown_hook",
        payload,
        debug_writer=_debug_writer,
        policy=_TestPolicy(),
        counters=counters,
    )

    assert result == {"value": 1}
    assert counters["unknown_hook"] == 1


@pytest.mark.asyncio
async def test_trace_by_call_id_orders_entries(project_config: ProjectConfig):
    now = datetime.now(UTC)
    rows = [
        {
            "time_created": now,
            "debug_type_identifier": "hook:alpha",
            "jsonblob": '{"payload": {"post_time_ns": 10.5}, "hook": "alpha"}',
        },
        {
            "time_created": now + timedelta(milliseconds=1),
            "debug_type_identifier": "hook:beta",
            "jsonblob": '{"payload": {}, "hook": "beta"}',
        },
    ]
    pool = _PoolWrapper(_FakeTraceConn(rows))

    response = await trace_by_call_id("abc123", pool=pool, config=project_config)

    assert isinstance(response, TraceResponse)
    assert [entry.hook for entry in response.entries] == ["alpha", "beta"]
    assert response.entries[0].post_time_ns == 10


@pytest.mark.asyncio
async def test_trace_by_call_id_requires_database(project_config: ProjectConfig):
    with pytest.raises(HTTPException):
        await trace_by_call_id("abc123", pool=None, config=project_config)


@pytest.mark.asyncio
async def test_recent_call_ids_returns_summary(project_config: ProjectConfig):
    now = datetime.now(UTC)
    rows = [
        {
            "cid": "abc123",
            "cnt": 2,
            "latest": now,
        }
    ]
    pool = _PoolWrapper(_FakeConn(rows))

    result = await recent_call_ids(pool=pool, config=project_config)

    assert result == [CallIdInfo(call_id="abc123", count=2, latest=now)]


@pytest.mark.asyncio
async def test_get_hook_counters_exposes_state():
    counters = Counter({"a": 1})
    result = await get_hook_counters(counters=counters)
    assert result == {"a": 1}


@pytest.mark.asyncio
async def test_hook_generic_handles_extract_errors(monkeypatch):
    async def _debug_writer(*_, **__):
        return None

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "luthien_proxy.control_plane.hooks_routes.extract_call_id_for_hook",
        _raise,
    )

    policy = _TestPolicy()
    counters = Counter()
    payload = {"value": 1, "post_time_ns": 2}

    result = await hook_generic(
        "async_post_call_success_hook",
        payload,
        debug_writer=_debug_writer,
        policy=policy,
        counters=counters,
    )

    assert result == {"handled": {"value": 1}}
    assert counters["async_post_call_success_hook"] == 1


class _FailPolicy(LuthienPolicy):
    async def async_post_call_success_hook(self, **payload):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_hook_generic_wraps_policy_errors(monkeypatch):
    async def _debug_writer(*_, **__):
        return None

    policy = _FailPolicy()
    counters = Counter()

    with pytest.raises(HTTPException) as exc:
        await hook_generic(
            "async_post_call_success_hook",
            {"post_time_ns": 1},
            debug_writer=_debug_writer,
            policy=policy,
            counters=counters,
        )

    assert "hook_generic_error" in exc.value.detail


@pytest.mark.asyncio
async def test_recent_call_ids_skips_empty(project_config: ProjectConfig):
    now = datetime.now(UTC)
    rows = [
        {"cid": "", "cnt": 1, "latest": now},
        {"cid": "abc", "cnt": 2, "latest": now},
    ]
    pool = _PoolWrapper(_FakeConn(rows))

    result = await recent_call_ids(pool=pool, config=project_config)

    assert result == [CallIdInfo(call_id="abc", count=2, latest=now)]


class _ErrorPool:
    def connection(self):
        class _Ctx:
            async def __aenter__(self):
                raise RuntimeError("boom")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_recent_call_ids_logs_errors(project_config: ProjectConfig):
    pool = _ErrorPool()
    result = await recent_call_ids(pool=pool, config=project_config)
    assert result == []
