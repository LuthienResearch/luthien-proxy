from __future__ import annotations

from datetime import datetime as real_datetime
from datetime import timezone
from typing import Any

import pytest

from luthien_proxy.control_plane import hook_result_handler


class FakeQueue:
    def __init__(self) -> None:
        self.submissions: list[Any] = []

    def submit(self, coro: Any) -> None:
        self.submissions.append(coro)


@pytest.mark.asyncio
async def test_log_and_publish_hook_result_with_call_id(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_queue = FakeQueue()
    event_queue = FakeQueue()
    monkeypatch.setattr(hook_result_handler, "DEBUG_LOG_QUEUE", debug_queue)
    monkeypatch.setattr(hook_result_handler, "CONVERSATION_EVENT_QUEUE", event_queue)

    timestamp_ns = 1234
    sentinel_timestamp = real_datetime(2024, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(hook_result_handler.time, "time_ns", lambda: timestamp_ns)

    class _FixedDateTime:
        @staticmethod
        def now(tz: Any = None) -> Any:
            assert tz is timezone.utc
            return sentinel_timestamp

    monkeypatch.setattr(hook_result_handler, "datetime", _FixedDateTime)

    build_calls: list[dict[str, Any]] = []
    events = [{"id": "event-1"}, {"id": "event-2"}]

    def fake_build_conversation_events(**kwargs: Any) -> list[dict[str, Any]]:
        build_calls.append(kwargs)
        return events

    monkeypatch.setattr(hook_result_handler, "build_conversation_events", fake_build_conversation_events)

    record_calls: list[tuple[Any, Any]] = []

    def fake_record_conversation_events(db_pool: Any, built_events: Any) -> Any:
        async def _runner() -> None:
            record_calls.append((db_pool, built_events))

        return _runner()

    monkeypatch.setattr(hook_result_handler, "record_conversation_events", fake_record_conversation_events)

    publish_calls: list[tuple[Any, Any]] = []

    def fake_publish_conversation_event(redis_conn: Any, event: Any) -> Any:
        async def _runner() -> None:
            publish_calls.append((redis_conn, event))

        return _runner()

    monkeypatch.setattr(hook_result_handler, "publish_conversation_event", fake_publish_conversation_event)

    # Mock activity stream functions
    activity_event_calls: list[dict[str, Any]] = []

    def fake_build_activity_events(**kwargs: Any) -> list[dict[str, Any]]:
        activity_event_calls.append(kwargs)
        # Always returns 2 events (original + final)
        return [{"activity": "event1"}, {"activity": "event2"}]

    monkeypatch.setattr(hook_result_handler, "build_activity_events", fake_build_activity_events)

    activity_publish_calls: list[tuple[Any, Any]] = []

    def fake_publish_activity_event(redis_conn: Any, event: Any) -> Any:
        async def _runner() -> None:
            activity_publish_calls.append((redis_conn, event))

        return _runner()

    monkeypatch.setattr(hook_result_handler, "publish_activity_event", fake_publish_activity_event)

    debug_writer_calls: list[tuple[str, Any]] = []

    async def fake_debug_writer(key: str, payload: Any) -> None:
        debug_writer_calls.append((key, payload))

    db_pool = object()
    redis_conn = object()
    original_payload = {"input": "hello"}
    result_payload = {"output": "hi"}

    hook_result_handler.log_and_publish_hook_result(
        hook_name="post_prompt",
        call_id="call-123",
        trace_id="trace-abc",
        original_payload=original_payload,
        result_payload=result_payload,
        debug_writer=fake_debug_writer,
        redis_conn=redis_conn,
        db_pool=db_pool,
    )

    assert len(debug_queue.submissions) == 1
    # event_queue now has: 2 activity events + 1 record_conversation_events + len(events) publish_conversation_event
    assert len(event_queue.submissions) == 2 + 1 + len(events)

    await debug_queue.submissions[0]
    for coro in event_queue.submissions:
        await coro

    assert debug_writer_calls == [
        (
            "hook_result:post_prompt",
            {
                "hook": "post_prompt",
                "luthien_call_id": "call-123",
                "litellm_trace_id": "trace-abc",
                "original": original_payload,
                "result": result_payload,
                "post_time_ns": timestamp_ns,
            },
        )
    ]

    assert build_calls == [
        {
            "hook": "post_prompt",
            "call_id": "call-123",
            "trace_id": "trace-abc",
            "original": original_payload,
            "result": result_payload,
            "timestamp_ns_fallback": timestamp_ns,
            "timestamp": sentinel_timestamp,
        }
    ]

    assert record_calls == [(db_pool, events)]
    assert publish_calls == [(redis_conn, events[0]), (redis_conn, events[1])]


@pytest.mark.asyncio
async def test_log_and_publish_hook_result_without_call_id(monkeypatch: pytest.MonkeyPatch) -> None:
    debug_queue = FakeQueue()
    event_queue = FakeQueue()
    monkeypatch.setattr(hook_result_handler, "DEBUG_LOG_QUEUE", debug_queue)
    monkeypatch.setattr(hook_result_handler, "CONVERSATION_EVENT_QUEUE", event_queue)

    monkeypatch.setattr(hook_result_handler.time, "time_ns", lambda: 999)

    class _FixedDateTime:
        @staticmethod
        def now(tz: Any = None) -> Any:
            assert tz is timezone.utc
            return real_datetime(2024, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(hook_result_handler, "datetime", _FixedDateTime)

    def fail_build(**kwargs: Any) -> None:
        raise AssertionError("build_conversation_events should not run without call_id")

    monkeypatch.setattr(hook_result_handler, "build_conversation_events", fail_build)

    # Mock activity stream functions (still called even without call_id)
    activity_event_calls: list[dict[str, Any]] = []

    def fake_build_activity_events(**kwargs: Any) -> list[dict[str, Any]]:
        activity_event_calls.append(kwargs)
        # Even without policy changes, we emit 2 events (original + final)
        return [{"activity": "event1"}, {"activity": "event2"}]

    monkeypatch.setattr(hook_result_handler, "build_activity_events", fake_build_activity_events)

    activity_publish_calls: list[tuple[Any, Any]] = []

    def fake_publish_activity_event(redis_conn: Any, event: Any) -> Any:
        async def _runner() -> None:
            activity_publish_calls.append((redis_conn, event))

        return _runner()

    monkeypatch.setattr(hook_result_handler, "publish_activity_event", fake_publish_activity_event)

    debug_writer_calls: list[tuple[str, Any]] = []

    async def fake_debug_writer(key: str, payload: Any) -> None:
        debug_writer_calls.append((key, payload))

    hook_result_handler.log_and_publish_hook_result(
        hook_name="guardrail",
        call_id=None,
        trace_id=None,
        original_payload={"x": 1},
        result_payload={"y": 2},
        debug_writer=fake_debug_writer,
        redis_conn=object(),
        db_pool=None,
    )

    assert len(debug_queue.submissions) == 1
    # Even without call_id, we still publish to global activity stream (2 events)
    assert len(event_queue.submissions) == 2

    await debug_queue.submissions[0]
    for coro in event_queue.submissions:
        await coro

    assert debug_writer_calls[0][0] == "hook_result:guardrail"
    assert debug_writer_calls[0][1]["hook"] == "guardrail"
    assert debug_writer_calls[0][1]["luthien_call_id"] is None


def test_prepare_policy_payload_passes_through_for_kwargs() -> None:
    def handler(**payload: Any) -> Any:
        return payload

    payload = {"foo": 1, "bar": 2}
    result = hook_result_handler.prepare_policy_payload(handler, payload)
    assert result is payload


def test_prepare_policy_payload_filters_named_parameters() -> None:
    def handler(foo: int, bar: int) -> int:
        return foo + bar

    payload = {"foo": 1, "bar": 2, "extra": 99}
    result = hook_result_handler.prepare_policy_payload(handler, payload)
    assert result == {"foo": 1, "bar": 2}


def test_prepare_policy_payload_ignores_self_parameter() -> None:
    class Handler:
        def run(self, foo: int) -> int:
            return foo

    payload = {"foo": 1, "self": "ignore-me"}
    result = hook_result_handler.prepare_policy_payload(Handler.run, payload)
    assert result == {"foo": 1}
