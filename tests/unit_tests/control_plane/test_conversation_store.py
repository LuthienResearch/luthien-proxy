from datetime import datetime, timezone

import pytest

from luthien_proxy.control_plane.conversation.models import ConversationEvent
from luthien_proxy.control_plane.conversation.store import record_conversation_events


class _FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *params: object):
        self.executed.append((sql, params))
        return "OK"

    async def fetchrow(self, sql: str, *params: object):
        self.fetchrow_calls.append((sql, params))
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    class _Ctx:
        def __init__(self, conn: _FakeConn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def connection(self):
        return self._Ctx(self._conn)


def _make_tool_chunk_event() -> ConversationEvent:
    timestamp = datetime.now(tz=timezone.utc)
    payload = {
        "chunk_index": 0,
        "choice_index": 0,
        "delta": "",
        "tool_calls": [
            {
                "id": "tool-1",
                "name": "write_file",
                "arguments": "{}",
            }
        ],
        "tool_call_ids": ["tool-1"],
    }
    return ConversationEvent(
        call_id="call-1",
        trace_id="trace-1",
        event_type="final_chunk",
        sequence=100,
        timestamp=timestamp,
        hook="async_post_call_streaming_iterator_hook",
        payload=payload,
    )


@pytest.mark.asyncio
async def test_record_conversation_events_emits_completion_for_tool_calls():
    conn = _FakeConn()
    pool = _FakePool(conn)
    event = _make_tool_chunk_event()

    await record_conversation_events(pool, [event])

    insert_statements = [sql for sql, _ in conn.executed if "INSERT INTO conversation_events" in sql]
    assert len(insert_statements) >= 2, "expected original and synthetic completion events to be inserted"

    assert any("synthetic_tool_completion" in params for _, params in conn.executed if params), (
        "synthetic completion event not recorded"
    )

    updated_calls = [sql for sql, _ in conn.executed if "UPDATE conversation_calls" in sql]
    assert updated_calls, "call status should be updated for tool-only completion"
