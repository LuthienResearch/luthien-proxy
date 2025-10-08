import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from luthien_proxy.control_plane.hooks_routes import (
    CallIdInfo,
    ConversationMessageDiff,
    ConversationSnapshot,
    TraceConversationSnapshot,
    TraceResponse,
    conversation_snapshot,
    conversation_snapshot_by_trace,
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
        if len(args) >= 4:
            # sql, call_id, limit_plus_one, offset
            limit_plus_one = args[2]
            offset = args[3]
            end = offset + limit_plus_one
            return self._rows[offset:end]
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
    assert response.limit == 500
    assert response.offset == 0
    assert response.has_more is False
    assert response.next_offset is None


@pytest.mark.asyncio
async def test_trace_by_call_id_supports_pagination(project_config: ProjectConfig):
    now = datetime.now(UTC)
    rows = [
        {
            "time_created": now + timedelta(milliseconds=idx),
            "debug_type_identifier": f"hook:{idx}",
            "jsonblob": f'{{"payload": {{}}, "hook": "h{idx}"}}',
        }
        for idx in range(3)
    ]
    pool = _PoolWrapper(_FakeTraceConn(rows))

    response = await trace_by_call_id(
        "abc123",
        limit=2,
        offset=0,
        pool=pool,
        config=project_config,
    )

    assert len(response.entries) == 2
    assert response.entries[0].hook == "h0"
    assert response.has_more is True
    assert response.next_offset == 2

    response_next = await trace_by_call_id(
        "abc123",
        limit=2,
        offset=response.next_offset,
        pool=pool,
        config=project_config,
    )

    assert [entry.hook for entry in response_next.entries] == ["h2"]
    assert response_next.has_more is False
    assert response_next.next_offset is None


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
async def test_conversation_snapshot_builds_events(project_config: ProjectConfig):
    now = datetime.now(UTC)

    pre_payload = {
        "hook": "async_pre_call_hook",
        "litellm_call_id": "call-1",
        "litellm_trace_id": "trace-1",
        "post_time_ns": 1,
        "original": {
            "request_data": {
                "messages": [{"role": "user", "content": "Hello"}],
                "post_time_ns": 1,
            }
        },
        "result": {
            "request_data": {
                "messages": [{"role": "user", "content": "Hello sanitized"}],
                "post_time_ns": 2,
            }
        },
    }

    stream_payload = {
        "hook": "async_post_call_streaming_iterator_hook",
        "litellm_call_id": "call-1",
        "litellm_trace_id": "trace-1",
        "post_time_ns": 2,
        "original": {
            "response": {"choices": [{"index": 0, "delta": {"content": "Hi"}}]},
            "post_time_ns": 3,
        },
        "result": {
            "response": {"choices": [{"index": 0, "delta": {"content": "Hello!"}}]},
            "post_time_ns": 4,
        },
    }

    success_payload = {
        "hook": "async_post_call_success_hook",
        "litellm_call_id": "call-1",
        "litellm_trace_id": "trace-1",
        "post_time_ns": 3,
        "original": {
            "response": {"choices": [{"message": {"content": "Hi"}}]},
            "post_time_ns": 5,
        },
        "result": {
            "response": {"choices": [{"message": {"content": "Hello friend!"}}]},
            "post_time_ns": 6,
        },
    }

    rows = [
        {
            "time_created": now + timedelta(milliseconds=3),
            "debug_type_identifier": "hook_result:async_pre_call_hook",
            "jsonblob": json.dumps(pre_payload),
        },
        {
            "time_created": now + timedelta(milliseconds=1),
            "debug_type_identifier": "hook_result:async_post_call_streaming_iterator_hook",
            "jsonblob": json.dumps(stream_payload),
        },
        {
            "time_created": now + timedelta(milliseconds=2),
            "debug_type_identifier": "hook_result:async_post_call_success_hook",
            "jsonblob": json.dumps(success_payload),
        },
    ]

    pool = _PoolWrapper(_FakeTraceConn(rows))

    snapshot = await conversation_snapshot("call-1", pool=pool, config=project_config)

    assert isinstance(snapshot, ConversationSnapshot)
    assert snapshot.call_id == "call-1"
    assert snapshot.trace_id == "trace-1"
    assert [event.event_type for event in snapshot.events] == [
        "request_started",
        "original_chunk",
        "final_chunk",
        "request_completed",
    ]
    first_event = snapshot.events[0]
    assert first_event.payload["original_messages"][0]["content"] == "Hello"
    assert first_event.payload["final_messages"][0]["content"] == "Hello sanitized"
    assert snapshot.events[1].payload["delta"] == "Hi"
    assert snapshot.events[1].payload["chunk_index"] == 0
    assert snapshot.events[2].payload["delta"] == "Hello!"
    assert snapshot.events[2].payload["chunk_index"] == 0
    assert snapshot.events[-1].payload["final_response"] == "Hello friend!"
    assert len(snapshot.calls) == 1
    call_snapshot = snapshot.calls[0]
    assert call_snapshot.call_id == "call-1"
    assert call_snapshot.final_response == "Hello friend!"
    assert call_snapshot.original_response == "Hi"
    assert call_snapshot.chunk_count == 1
    assert call_snapshot.new_messages == [
        ConversationMessageDiff(role="user", original="Hello", final="Hello sanitized")
    ]
    assert call_snapshot.final_chunks == ["Hello friend!"]
    assert call_snapshot.original_chunks == ["Hi"]


@pytest.mark.asyncio
async def test_conversation_snapshot_by_trace_collects_calls(project_config: ProjectConfig):
    now = datetime.now(UTC)

    call1_payload = {
        "hook": "async_post_call_success_hook",
        "litellm_call_id": "call-1",
        "litellm_trace_id": "trace-1",
        "original": {
            "response": {"choices": [{"message": {"content": "Hi"}}]},
            "post_time_ns": 5,
        },
        "result": {
            "response": {"choices": [{"message": {"content": "Hello"}}]},
            "post_time_ns": 6,
        },
    }

    call2_payload = {
        "hook": "async_post_call_success_hook",
        "litellm_call_id": "call-2",
        "litellm_trace_id": "trace-1",
        "original": {
            "response": {"choices": [{"message": {"content": "Howdy"}}]},
            "post_time_ns": 7,
        },
        "result": {
            "response": {"choices": [{"message": {"content": "Howdy partner"}}]},
            "post_time_ns": 8,
        },
    }

    rows = [
        {
            "time_created": now,
            "debug_type_identifier": "hook_result:async_post_call_success_hook",
            "jsonblob": json.dumps(call1_payload),
        },
        {
            "time_created": now + timedelta(milliseconds=1),
            "debug_type_identifier": "hook_result:async_post_call_success_hook",
            "jsonblob": json.dumps(call2_payload),
        },
    ]

    pool = _PoolWrapper(_FakeTraceConn(rows))

    snapshot = await conversation_snapshot_by_trace("trace-1", pool=pool, config=project_config)

    assert isinstance(snapshot, TraceConversationSnapshot)
    assert snapshot.trace_id == "trace-1"
    assert snapshot.call_ids == ["call-1", "call-2"]
    assert len(snapshot.events) == 2
    assert [call.call_id for call in snapshot.calls] == ["call-1", "call-2"]
    response_map = {call.call_id: call.final_response for call in snapshot.calls}
    assert response_map == {"call-1": "Hello", "call-2": "Howdy partner"}
    assert snapshot.calls[0].final_chunks == ["Hello"]
    assert snapshot.calls[1].final_chunks == ["Howdy partner"]


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
