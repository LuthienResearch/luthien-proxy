"""Tests for conversation history service layer.

Tests the pure business logic functions for fetching sessions,
parsing conversation turns, and exporting to markdown.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.history import service
from luthien_proxy.history.models import (
    ConversationMessage,
    ConversationTurn,
    MessageType,
    PolicyAnnotation,
    SessionDetail,
    SessionSearchParams,
)
from luthien_proxy.history.service import (
    CallEventRange,
    RequestProjection,
    StoredEvent,
    _build_turn,
    _extract_preview_message,
    _extract_tool_calls,
    _get_event_summary,
    _parse_request_messages,
    _parse_response_messages,
    _safe_parse_json,
    export_session_jsonl,
    export_session_markdown,
    extract_text_content,
    fetch_session_detail,
    fetch_session_list,
    iter_session_turns,
    stream_session_detail_json,
)
from luthien_proxy.utils.db import DatabasePool, parse_db_ts
from luthien_proxy.utils.db_sqlite import SqliteConnection


async def _collect_bytes(chunks) -> bytes:
    parts: list[bytes] = []
    async for chunk in chunks:
        if isinstance(chunk, str):
            parts.append(chunk.encode())
        else:
            parts.append(chunk)
    return b"".join(parts)


def _enable_mock_transaction(mock_conn: AsyncMock) -> None:
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)


@pytest.fixture
async def sqlite_pool() -> AsyncIterator[DatabasePool]:
    pool = DatabasePool("sqlite://:memory:")
    migrations_dir = Path(__file__).parent.parent.parent.parent.parent / "migrations" / "sqlite"

    async with pool.connection() as conn:
        assert isinstance(conn, SqliteConnection)
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            await conn.executescript(migration_file.read_text())

    yield pool

    await pool.close()


def _equivalence_rows() -> list[dict[str, object]]:
    return [
        {
            "call_id": "call-1",
            "event_type": "transaction.request_recorded",
            "payload": {
                "final_model": "gpt-4",
                "original_request": {"messages": [{"role": "user", "content": "Hi"}]},
                "final_request": {"messages": [{"role": "user", "content": "Hi"}]},
            },
            "created_at": datetime(2025, 1, 15, 10, 0, 0),
        },
        {
            "call_id": "call-1",
            "event_type": "transaction.streaming_response_recorded",
            "payload": {
                "original_response": {"choices": [{"message": {"content": "Hello!"}}]},
                "final_response": {"choices": [{"message": {"content": "Hello!"}}]},
            },
            "created_at": datetime(2025, 1, 15, 10, 0, 1),
        },
        {
            "call_id": "call-2",
            "event_type": "transaction.request_recorded",
            "payload": {
                "final_model": "claude-3-sonnet",
                "original_request": {"messages": [{"role": "user", "content": "Hi"}]},
                "final_request": {
                    "messages": [
                        {"role": "user", "content": "Hi"},
                        {"role": "assistant", "content": "Hello!"},
                        {"role": "user", "content": "Use the tool"},
                    ]
                },
            },
            "created_at": datetime(2025, 1, 15, 10, 2, 0),
        },
        {
            "call_id": "call-2",
            "event_type": "policy.anthropic_judge.tool_call_blocked",
            "payload": {"summary": "Dangerous operation blocked", "rule": "deny"},
            "created_at": datetime(2025, 1, 15, 10, 2, 1),
        },
        {
            "call_id": "call-2",
            "event_type": "transaction.non_streaming_response_recorded",
            "payload": {
                "original_response": {"choices": [{"message": {"content": "Done"}}]},
                "final_response": {"choices": [{"message": {"content": "Blocked"}}]},
            },
            "created_at": datetime(2025, 1, 15, 10, 2, 2),
        },
    ]


def _projection(
    call_id: str,
    raw_msg_count: int,
    created_at: datetime,
    *,
    max_tokens: int | None = None,
    output_config: dict[str, object] | None = None,
    request_was_modified: bool = False,
    tools_count: int | None = None,
) -> RequestProjection:
    return service.RequestProjection(
        call_id=call_id,
        first_ts=created_at,
        request_ts=created_at,
        final_model=DEFAULT_TEST_MODEL,
        request_params={
            "model": DEFAULT_TEST_MODEL,
            "max_tokens": max_tokens,
            "output_config": output_config,
            "tools_count": tools_count,
        },
        raw_msg_count=raw_msg_count,
        request_was_modified=request_was_modified,
    )


def _raw_messages(contents: list[str]) -> list[dict[str, object]]:
    return [{"role": "user", "content": content} for content in contents]


def _request_event(call_id: str, created_at: datetime, messages: list[dict[str, object]]) -> StoredEvent:
    return StoredEvent(
        event_type="transaction.request_recorded",
        payload={
            "final_model": DEFAULT_TEST_MODEL,
            "final_request": {"model": DEFAULT_TEST_MODEL, "messages": messages},
        },
        created_at=created_at,
    )


def _response_event(content: str, created_at: datetime) -> StoredEvent:
    return StoredEvent(
        event_type="transaction.non_streaming_response_recorded",
        payload={"final_response": {"choices": [{"message": {"content": content}}]}},
        created_at=created_at,
    )


def _window_rows(call_count: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    messages: list[dict[str, object]] = []
    base_time = datetime(2025, 1, 15, 10, 0, 0)
    for index in range(call_count):
        call_id = f"window-call-{index + 1}"
        messages = [*messages, {"role": "user", "content": f"message {index + 1}"}]
        request_time = base_time + timedelta(minutes=index)
        rows.append(
            {
                "call_id": call_id,
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": DEFAULT_TEST_MODEL,
                    "final_request": {
                        "model": DEFAULT_TEST_MODEL,
                        "messages": messages,
                        "stream": True,
                    },
                },
                "created_at": request_time,
            }
        )
        rows.append(
            {
                "call_id": call_id,
                "event_type": "transaction.non_streaming_response_recorded",
                "payload": {"final_response": {"choices": [{"message": {"content": f"response {index + 1}"}}]}},
                "created_at": request_time + timedelta(seconds=1),
            }
        )
    return rows


def _range_rows_from_event_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["call_id"]), []).append(row)
    return [
        {
            "call_id": call_id,
            "first_ts": min(parse_db_ts(row["created_at"]) for row in call_rows),
            "last_ts": max(parse_db_ts(row["created_at"]) for row in call_rows),
        }
        for call_id, call_rows in grouped.items()
    ]


def _projection_rows_from_event_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    first_ts_by_call_id: dict[str, datetime] = {}
    for range_row in _range_rows_from_event_rows(rows):
        first_ts_by_call_id[str(range_row["call_id"])] = parse_db_ts(range_row["first_ts"])
    projection_rows: list[dict[str, object]] = []
    for row in rows:
        if row["event_type"] != "transaction.request_recorded":
            continue
        payload = row["payload"]
        if not isinstance(payload, dict):
            continue
        final_request = payload.get("final_request")
        original_request = payload.get("original_request")
        if not isinstance(final_request, dict):
            continue
        projection_rows.append(
            {
                "call_id": row["call_id"],
                "first_ts": first_ts_by_call_id[str(row["call_id"])],
                "request_ts": row["created_at"],
                "final_model": payload.get("final_model"),
                "model": final_request.get("model"),
                "max_tokens": final_request.get("max_tokens"),
                "stream": final_request.get("stream"),
                "temperature": final_request.get("temperature"),
                "top_p": final_request.get("top_p"),
                "top_k": final_request.get("top_k"),
                "stop_sequences": final_request.get("stop_sequences"),
                "output_config": final_request.get("output_config"),
                "tools_count": len(final_request["tools"]) if isinstance(final_request.get("tools"), list) else None,
                "raw_msg_count": len(final_request["messages"])
                if isinstance(final_request.get("messages"), list)
                else 0,
                "request_was_modified": original_request is not None and original_request != final_request,
            }
        )
    return projection_rows


def _message_rows_from_event_rows(rows: list[dict[str, object]], call_ids: list[str]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in rows:
        if row["event_type"] != "transaction.request_recorded" or row["call_id"] not in call_ids:
            continue
        payload = row["payload"]
        if not isinstance(payload, dict):
            continue
        final_request = payload.get("final_request")
        if isinstance(final_request, dict):
            output.append({"call_id": row["call_id"], "messages": final_request.get("messages", [])})
    return output


def _summary_row_from_event_rows(rows: list[dict[str, object]]) -> dict[str, object] | None:
    if not rows:
        return None
    return {
        "first_ts": min(parse_db_ts(row["created_at"]) for row in rows),
        "last_ts": max(parse_db_ts(row["created_at"]) for row in rows),
        "total_turns": len(
            {str(row["call_id"]) for row in rows if row["event_type"] == "transaction.request_recorded"}
        ),
    }


def _session_stats_row_from_event_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    models: set[str] = set()
    policy_interventions = 0
    for row in rows:
        event_type = str(row["event_type"])
        if event_type.startswith("policy.") and "judge.evaluation" not in event_type:
            policy_interventions += 1
        if event_type != "transaction.request_recorded":
            continue
        payload = row["payload"]
        if not isinstance(payload, dict):
            continue
        model = payload.get("final_model")
        if model is not None:
            models.add(str(model))
    return {"policy_interventions": policy_interventions, "models_used": ",".join(sorted(models))}


def _request_window_rows_from_event_rows(
    rows: list[dict[str, object]], limit: int, offset: int
) -> list[dict[str, object]]:
    request_rows = [row for row in rows if row["event_type"] == "transaction.request_recorded"]
    return [
        {"call_id": row["call_id"], "request_ts": row["created_at"]}
        for row in sorted(request_rows, key=lambda row: parse_db_ts(row["created_at"]))[offset : offset + limit]
    ]


def _previous_raw_msg_count_from_event_rows(
    rows: list[dict[str, object]], before_ts: object
) -> dict[str, object] | None:
    previous_rows = []
    for row in rows:
        if row["event_type"] != "transaction.request_recorded":
            continue
        payload = row["payload"]
        if not isinstance(payload, dict):
            continue
        final_request = payload.get("final_request")
        if not isinstance(final_request, dict):
            continue
        max_tokens = final_request.get("max_tokens")
        output_config = final_request.get("output_config")
        output_format = output_config.get("format") if isinstance(output_config, dict) else None
        if max_tokens == 1 or (
            isinstance(output_format, dict) and output_format.get("type") == "json_schema" and max_tokens <= 256
        ):
            continue
        if parse_db_ts(row["created_at"]) < parse_db_ts(before_ts):
            previous_rows.append(row)
    if not previous_rows:
        return None
    previous_row = max(previous_rows, key=lambda row: parse_db_ts(row["created_at"]))
    payload = previous_row["payload"]
    if not isinstance(payload, dict):
        return None
    final_request = payload.get("final_request")
    if not isinstance(final_request, dict):
        return None
    messages = final_request.get("messages")
    if not isinstance(messages, list):
        return {"raw_msg_count": 0}
    return {"raw_msg_count": len(messages)}


def _fetchrow_from_event_rows(rows: list[dict[str, object]]):
    def fetch_row(query: str, _session_id: str, *args: object):
        lowered = query.lower()
        if "from session_summaries" in lowered:
            return None
        if "count(distinct case" in lowered:
            return _summary_row_from_event_rows(rows)
        if "policy_interventions" in lowered and "models_used" in lowered:
            return _session_stats_row_from_event_rows(rows)
        if "raw_msg_count" in lowered:
            return _previous_raw_msg_count_from_event_rows(rows, args[0])
        return None

    return fetch_row


def _fetch_from_event_rows(rows: list[dict[str, object]]):
    def fetch_rows(query: str, _session_id: str, *args: object):
        lowered = query.lower()
        if "raw_msg_count" in lowered:
            projection_rows = _projection_rows_from_event_rows(rows)
            if len(args) == 2 and all(isinstance(arg, int) for arg in args):
                limit = int(args[0])
                offset = int(args[1])
                return sorted(projection_rows, key=lambda row: parse_db_ts(row["request_ts"]))[offset : offset + limit]
            return projection_rows
        if "created_at as request_ts" in lowered:
            limit = int(args[0])
            offset = int(args[1])
            return _request_window_rows_from_event_rows(rows, limit, offset)
        if "group by call_id" in lowered:
            return _range_rows_from_event_rows(rows)
        call_ids = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
        if " as messages" in lowered:
            return _message_rows_from_event_rows(rows, [str(call_id) for call_id in call_ids])
        if "event_type <> 'transaction.request_recorded'" in lowered:
            return [
                row
                for row in rows
                if row["event_type"] != "transaction.request_recorded" and row["call_id"] in call_ids
            ]
        return [row for row in rows if row["call_id"] in call_ids]

    return fetch_rows


def _stub_event_rows(mock_conn: AsyncMock, rows: list[dict[str, object]]) -> None:
    mock_conn.fetch.side_effect = _fetch_from_event_rows(rows)
    mock_conn.fetchrow.side_effect = _fetchrow_from_event_rows(rows)


def _assert_turns_field_equal(actual: list[ConversationTurn], expected: list[ConversationTurn]) -> None:
    assert len(actual) == len(expected)
    for actual_turn, expected_turn in zip(actual, expected, strict=True):
        assert actual_turn.model_dump_json() == expected_turn.model_dump_json()


def _representative_fast_path_rows() -> list[dict[str, object]]:
    def request_payload(
        messages: list[dict[str, object]],
        *,
        max_tokens: int = 1024,
        output_config: dict[str, object] | None = None,
        stop_sequences: list[str] | None = None,
    ) -> dict[str, object]:
        final_request: dict[str, object] = {
            "model": DEFAULT_TEST_MODEL,
            "max_tokens": max_tokens,
            "stream": True,
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 40,
            "messages": messages,
            "tools": [{"name": "lookup"}, {"name": "calendar"}],
        }
        if output_config is not None:
            final_request["output_config"] = output_config
        if stop_sequences is not None:
            final_request["stop_sequences"] = stop_sequences
        return {"final_model": DEFAULT_TEST_MODEL, "final_request": final_request, "original_request": final_request}

    cumulative_messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "need lookup"},
                {"type": "tool_use", "id": "tool-1", "name": "lookup", "input": {"city": "Oslo"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "snow"},
                {"type": "text", "text": "thanks"},
            ],
        },
        {"role": "user", "content": "second"},
    ]
    malformed_output_config = {"format": "json_schema"}
    return [
        {
            "call_id": "call-1",
            "event_type": "policy.string_replacement.request_modified",
            "payload": {"summary": "request warning", "rule": "context"},
            "created_at": datetime(2025, 1, 15, 9, 59, 59),
        },
        {
            "call_id": "call-1",
            "event_type": "transaction.request_recorded",
            "payload": request_payload(
                [cumulative_messages[0]],
                output_config=malformed_output_config,
                stop_sequences=["END"],
            ),
            "created_at": datetime(2025, 1, 15, 10, 0, 0),
        },
        {
            "call_id": "call-1",
            "event_type": "transaction.non_streaming_response_recorded",
            "payload": {
                "original_response": {"choices": [{"message": {"content": "blocked"}}]},
                "final_response": {"choices": [{"message": {"content": "allowed"}}]},
            },
            "created_at": datetime(2025, 1, 15, 10, 0, 1),
        },
        {
            "call_id": "preflight-mid",
            "event_type": "transaction.request_recorded",
            "payload": request_payload([{"role": "user", "content": "quota"}], max_tokens=1),
            "created_at": datetime(2025, 1, 15, 10, 0, 2),
        },
        {
            "call_id": "preflight-mid",
            "event_type": "transaction.non_streaming_response_recorded",
            "payload": {"final_response": {"choices": [{"message": {"content": "ok"}}]}},
            "created_at": datetime(2025, 1, 15, 10, 0, 3),
        },
        {
            "call_id": "call-2",
            "event_type": "transaction.request_recorded",
            "payload": request_payload(cumulative_messages),
            "created_at": datetime(2025, 1, 15, 10, 0, 4),
        },
        {
            "call_id": "call-2",
            "event_type": "policy.judge.tool_call_blocked",
            "payload": {"summary": "blocked tool", "tool": "lookup"},
            "created_at": datetime(2025, 1, 15, 10, 0, 5),
        },
        {
            "call_id": "call-2",
            "event_type": "transaction.streaming_response_recorded",
            "payload": {"final_response": {"choices": [{"message": {"content": "done"}}]}},
            "created_at": datetime(2025, 1, 15, 10, 0, 6),
        },
        {
            "call_id": "preflight-last",
            "event_type": "transaction.request_recorded",
            "payload": request_payload(
                [{"role": "user", "content": "schema probe"}],
                max_tokens=128,
                output_config={"format": {"type": "json_schema", "schema": {"private": True}}},
            ),
            "created_at": datetime(2025, 1, 15, 10, 0, 7),
        },
    ]


async def _insert_sqlite_call(conn: SqliteConnection, call_id: str, session_id: str, created_at: str) -> None:
    await conn.execute(
        "INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        call_id,
        DEFAULT_TEST_MODEL,
        "openai",
        "completed",
        session_id,
        created_at,
    )


async def _insert_sqlite_event_rows(conn: SqliteConnection, session_id: str, rows: list[dict[str, object]]) -> None:
    inserted_call_ids: set[str] = set()
    for index, row in enumerate(rows):
        call_id = str(row["call_id"])
        created_at = parse_db_ts(row["created_at"]).isoformat()
        if call_id not in inserted_call_ids:
            await _insert_sqlite_call(conn, call_id, session_id, created_at)
            inserted_call_ids.add(call_id)
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            f"{session_id}-{index}-{row['call_id']}-{row['event_type']}",
            row["call_id"],
            row["event_type"],
            json.dumps(row["payload"]),
            session_id,
            created_at,
        )


class TestReadFinalOnceFastPath:
    @pytest.mark.asyncio
    async def test_fast_path_reconstructs_real_turn_deltas_from_one_anchor_and_preflights(self):
        created = [datetime(2025, 1, 15, 10, index, 0) for index in range(4)]
        projections = [
            _projection("call-1", 1, created[0]),
            _projection("preflight", 1, created[1], max_tokens=1),
            _projection("call-2", 3, created[2]),
            _projection("call-3", 5, created[3]),
        ]
        events_by_call_id = {
            "call-1": [_response_event("r1", created[0])],
            "preflight": [_response_event("probe", created[1])],
            "call-2": [_response_event("r2", created[2])],
            "call-3": [_response_event("r3", created[3])],
        }
        messages_by_call_id = {
            "call-3": _raw_messages(["one", "assistant one", "two", "assistant two", "three"]),
            "preflight": _raw_messages(["quota probe"]),
        }

        turns = [
            turn
            async for turn in service._iter_session_turns_fast(
                projections,
                events_by_call_id,
                messages_by_call_id,
            )
        ]

        assert [[message.content for message in turn.request_messages] for turn in turns] == [
            ["one"],
            ["quota probe"],
            ["assistant one", "two"],
            ["assistant two", "three"],
        ]
        assert [turn.request_messages_full for turn in turns] == [None, None, None, None]
        assert [turn.response_messages[0].content for turn in turns] == ["r1", "probe", "r2", "r3"]

    def test_fast_anchor_uses_largest_real_turn_not_last_preflight(self):
        created = [datetime(2025, 1, 15, 10, index, 0) for index in range(3)]
        projections = [
            _projection("call-1", 1, created[0]),
            _projection("call-2", 5, created[1]),
            _projection("title-gen", 2, created[2], max_tokens=128, output_config={"format": {"type": "json_schema"}}),
        ]

        plan = service._select_fast_path_anchor(projections)

        assert plan is not None
        assert plan.anchor.call_id == "call-2"
        assert [projection.call_id for projection in plan.preflights] == ["title-gen"]

    def test_fast_anchor_rejects_request_modified_real_turn(self):
        created = datetime(2025, 1, 15, 10, 0, 0)
        projections = [
            _projection("call-1", 1, created),
            _projection("call-2", 3, created.replace(minute=1), request_was_modified=True),
        ]

        assert service._select_fast_path_anchor(projections) is None

    def test_fast_anchor_rejects_non_monotonic_real_message_counts(self):
        created = datetime(2025, 1, 15, 10, 0, 0)
        projections = [
            _projection("call-1", 5, created),
            _projection("call-2", 3, created.replace(minute=1)),
        ]

        assert service._select_fast_path_anchor(projections) is None

    @pytest.mark.asyncio
    async def test_fast_path_uses_raw_to_parsed_prefix_boundaries_for_tool_expansion(self):
        created = [datetime(2025, 1, 15, 10, index, 0) for index in range(2)]
        projections = [_projection("call-1", 1, created[0]), _projection("call-2", 3, created[1])]
        events_by_call_id = {
            "call-1": [_response_event("r1", created[0])],
            "call-2": [_response_event("r2", created[1])],
        }
        anchor_messages: list[dict[str, object]] = [
            {"role": "user", "content": "weather"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Oslo"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tool-1", "content": "snow"},
        ]

        turns = [
            turn
            async for turn in service._iter_session_turns_fast(
                projections,
                events_by_call_id,
                {"call-2": anchor_messages},
            )
        ]

        assert [message.content for message in turns[0].request_messages] == ["weather"]
        assert [message.message_type for message in turns[1].request_messages] == [
            MessageType.TOOL_CALL,
            MessageType.TOOL_RESULT,
        ]
        assert [message.content for message in turns[1].request_messages] == ['{"city":"Oslo"}', "snow"]

    @pytest.mark.asyncio
    async def test_fast_path_preserves_build_turn_request_params_shape(self):
        created = datetime(2025, 1, 15, 10, 0, 0)
        projection = _projection(
            "call-1",
            1,
            created,
            max_tokens=1024,
            output_config={"format": {"type": "json_schema"}},
            tools_count=2,
        )

        turns = [
            turn
            async for turn in service._iter_session_turns_fast(
                [projection],
                {"call-1": [_response_event("ok", created)]},
                {"call-1": _raw_messages(["one"])},
            )
        ]

        assert turns[0].request_params == {
            "model": DEFAULT_TEST_MODEL,
            "max_tokens": 1024,
            "output_config": {"format": {"type": "json_schema"}},
            "tools_count": 2,
        }

    @pytest.mark.asyncio
    async def test_iter_session_turns_fast_path_fetches_only_one_large_messages_array(self, monkeypatch):
        created = [datetime(2025, 1, 15, 10, index, 0) for index in range(3)]
        ranges = [CallEventRange(f"call-{index + 1}", created[index], created[index]) for index in range(3)]
        projection_rows = [
            {
                "call_id": "call-1",
                "first_ts": created[0],
                "request_ts": created[0],
                "final_model": DEFAULT_TEST_MODEL,
                "model": DEFAULT_TEST_MODEL,
                "max_tokens": None,
                "stream": None,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "stop_sequences": None,
                "output_config": None,
                "tools_count": None,
                "raw_msg_count": 1,
                "request_was_modified": False,
            },
            {
                "call_id": "call-2",
                "first_ts": created[1],
                "request_ts": created[1],
                "final_model": DEFAULT_TEST_MODEL,
                "model": DEFAULT_TEST_MODEL,
                "max_tokens": None,
                "stream": None,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "stop_sequences": None,
                "output_config": None,
                "tools_count": None,
                "raw_msg_count": 3,
                "request_was_modified": False,
            },
            {
                "call_id": "call-3",
                "first_ts": created[2],
                "request_ts": created[2],
                "final_model": DEFAULT_TEST_MODEL,
                "model": DEFAULT_TEST_MODEL,
                "max_tokens": None,
                "stream": None,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "stop_sequences": None,
                "output_config": None,
                "tools_count": None,
                "raw_msg_count": 5,
                "request_was_modified": False,
            },
        ]
        payload_rows = {
            "call-1": [
                {
                    "call_id": "call-1",
                    "event_type": "transaction.non_streaming_response_recorded",
                    "payload": {"final_response": {"choices": [{"message": {"content": "r1"}}]}},
                    "created_at": created[0],
                }
            ],
            "call-2": [
                {
                    "call_id": "call-2",
                    "event_type": "transaction.non_streaming_response_recorded",
                    "payload": {"final_response": {"choices": [{"message": {"content": "r2"}}]}},
                    "created_at": created[1],
                }
            ],
            "call-3": [
                {
                    "call_id": "call-3",
                    "event_type": "transaction.non_streaming_response_recorded",
                    "payload": {"final_response": {"choices": [{"message": {"content": "r3"}}]}},
                    "created_at": created[2],
                }
            ],
        }
        messages_rows = [
            {
                "call_id": "call-3",
                "messages": json.dumps(_raw_messages(["one", "two", "three", "four", "five"])),
            }
        ]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)

        def fetch_side_effect(query: str, _session_id: str, *args: object):
            lowered = query.lower()
            if "raw_msg_count" in lowered:
                return projection_rows
            if " as messages" in lowered:
                return messages_rows
            call_ids = args[0] if len(args) == 1 and isinstance(args[0], list) else list(args)
            return [row for call_id in call_ids for row in payload_rows[str(call_id)]]

        mock_conn.fetch.side_effect = fetch_side_effect
        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn
        raw_parse_count = 0
        request_parse_count = 0
        original_parse_raw_request_message = service._parse_raw_request_message
        original_parse_request_messages = service._parse_request_messages

        def count_raw_parse(raw_message: dict[str, object]) -> list[ConversationMessage]:
            nonlocal raw_parse_count
            raw_parse_count += 1
            return original_parse_raw_request_message(raw_message)

        def count_request_parse(request: dict[str, object]) -> list[ConversationMessage]:
            nonlocal request_parse_count
            raw_messages = request.get("messages", [])
            if isinstance(raw_messages, list):
                request_parse_count += len(raw_messages)
            return original_parse_request_messages(request)

        monkeypatch.setattr(service, "_parse_raw_request_message", count_raw_parse)
        monkeypatch.setattr(service, "_parse_request_messages", count_request_parse)

        turns = [turn async for turn in iter_session_turns("session-1", mock_pool, ranges)]

        messages_fetches = [
            call.args[0] for call in mock_conn.fetch.call_args_list if " as messages" in call.args[0].lower()
        ]
        full_payload_fetches = [
            call.args[0]
            for call in mock_conn.fetch.call_args_list
            if "select call_id, event_type, payload" in call.args[0].lower()
        ]
        assert [[message.content for message in turn.request_messages] for turn in turns] == [
            ["one"],
            ["two", "three"],
            ["four", "five"],
        ]
        assert len(messages_fetches) == 1
        assert len(full_payload_fetches) == 1
        assert raw_parse_count + request_parse_count <= 5

    @pytest.mark.asyncio
    async def test_sqlite_projection_and_message_fetches_use_json_projection(self, sqlite_pool: DatabasePool):
        async with sqlite_pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_calls (call_id, model_name, provider, status, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "call-1",
                DEFAULT_TEST_MODEL,
                "openai",
                "completed",
                "session-fast",
                "2025-01-15T10:00:00",
            )
            await conn.execute(
                """
                INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "event-1",
                "call-1",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": DEFAULT_TEST_MODEL,
                        "final_request": {
                            "model": DEFAULT_TEST_MODEL,
                            "max_tokens": 512,
                            "stream": True,
                            "output_config": {"format": {"type": "json_schema", "schema": {"secret": True}}},
                            "tools": [{"name": "a"}, {"name": "b"}],
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                    }
                ),
                "session-fast",
                "2025-01-15T10:00:00",
            )

            projections = await service._fetch_request_projections(
                conn,
                "session-fast",
                [CallEventRange("call-1", datetime(2025, 1, 15, 10, 0, 0), datetime(2025, 1, 15, 10, 0, 0))],
                is_sqlite=True,
            )
            messages_by_call = await service._fetch_request_messages_by_call_id(
                conn,
                "session-fast",
                ["call-1"],
                is_sqlite=True,
            )

        assert projections == [
            service.RequestProjection(
                call_id="call-1",
                first_ts=datetime(2025, 1, 15, 10, 0, 0),
                request_ts=datetime(2025, 1, 15, 10, 0, 0),
                final_model=DEFAULT_TEST_MODEL,
                request_params={
                    "model": DEFAULT_TEST_MODEL,
                    "max_tokens": 512,
                    "stream": True,
                    "output_config": {"format": {"type": "json_schema"}},
                    "tools_count": 2,
                },
                raw_msg_count=1,
                request_was_modified=False,
            )
        ]
        assert messages_by_call == {"call-1": [{"role": "user", "content": "hello"}]}

    @pytest.mark.asyncio
    async def test_fast_turns_match_slow_turns_for_representative_pg_rows(self):
        rows = _representative_fast_path_rows()
        ranges = [
            CallEventRange(
                call_id=str(row["call_id"]),
                first_ts=parse_db_ts(row["first_ts"]),
                last_ts=parse_db_ts(row["last_ts"]),
            )
            for row in _range_rows_from_event_rows(rows)
        ]
        fast_conn = AsyncMock()
        fast_conn.fetch.side_effect = _fetch_from_event_rows(rows)
        projections = await service._fetch_request_projections(fast_conn, "session-parity", ranges, is_sqlite=False)
        plan = service._select_fast_path_anchor(projections)
        assert plan is not None
        messages_by_call = await service._fetch_request_messages_by_call_id(
            fast_conn,
            "session-parity",
            [plan.anchor.call_id, *(projection.call_id for projection in plan.preflights)],
            is_sqlite=False,
        )
        event_rows_by_call_id = await service._fetch_non_request_event_rows(
            fast_conn,
            "session-parity",
            ranges,
            is_sqlite=False,
        )
        events_by_call_id = {
            call_id: service._stored_events_from_rows(event_rows)
            for call_id, event_rows in event_rows_by_call_id.items()
        }

        slow_conn = AsyncMock()
        _enable_mock_transaction(slow_conn)
        slow_conn.fetch.side_effect = _fetch_from_event_rows(rows)
        slow_pool = MagicMock()
        slow_pool.is_sqlite = False
        slow_pool.connection.return_value.__aenter__.return_value = slow_conn

        fast_turns = [
            turn async for turn in service._iter_session_turns_fast(projections, events_by_call_id, messages_by_call)
        ]
        slow_turns = [turn async for turn in service._iter_session_turns_slow("session-parity", slow_pool, ranges)]

        _assert_turns_field_equal(fast_turns, slow_turns)

    @pytest.mark.asyncio
    async def test_fast_turns_match_slow_turns_for_representative_sqlite_rows(self, sqlite_pool: DatabasePool):
        rows = _representative_fast_path_rows()
        async with sqlite_pool.connection() as conn:
            inserted_call_ids: set[str] = set()
            for index, row in enumerate(rows, start=1):
                call_id = str(row["call_id"])
                if call_id not in inserted_call_ids:
                    await _insert_sqlite_call(
                        conn,
                        call_id,
                        "session-sqlite-parity",
                        parse_db_ts(row["created_at"]).isoformat(),
                    )
                    inserted_call_ids.add(call_id)
                await conn.execute(
                    """
                    INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"parity-event-{index}",
                    call_id,
                    row["event_type"],
                    json.dumps(row["payload"]),
                    "session-sqlite-parity",
                    parse_db_ts(row["created_at"]).isoformat(),
                )
        ranges = await service._fetch_call_ranges("session-sqlite-parity", sqlite_pool)
        async with sqlite_pool.connection() as conn:
            projections = await service._fetch_request_projections(
                conn,
                "session-sqlite-parity",
                ranges,
                is_sqlite=True,
            )
            plan = service._select_fast_path_anchor(projections)
            assert plan is not None
            messages_by_call = await service._fetch_request_messages_by_call_id(
                conn,
                "session-sqlite-parity",
                [plan.anchor.call_id, *(projection.call_id for projection in plan.preflights)],
                is_sqlite=True,
            )
            event_rows_by_call_id = await service._fetch_non_request_event_rows(
                conn,
                "session-sqlite-parity",
                ranges,
                is_sqlite=True,
            )
        events_by_call_id = {
            call_id: service._stored_events_from_rows(event_rows)
            for call_id, event_rows in event_rows_by_call_id.items()
        }
        fast_turns = [
            turn async for turn in service._iter_session_turns_fast(projections, events_by_call_id, messages_by_call)
        ]
        slow_turns = [
            turn async for turn in service._iter_session_turns_slow("session-sqlite-parity", sqlite_pool, ranges)
        ]

        _assert_turns_field_equal(fast_turns, slow_turns)

    @pytest.mark.asyncio
    async def test_fast_path_rejects_modified_preflight_projection(self):
        created = datetime(2025, 1, 15, 10, 0, 0)
        projections = [
            _projection("call-1", 1, created),
            _projection("preflight", 1, created, max_tokens=1, request_was_modified=True),
            _projection("call-2", 2, created),
        ]

        assert service._select_fast_path_anchor(projections) is None

    @pytest.mark.asyncio
    async def test_sqlite_projection_detects_request_modification_without_false_negative(
        self, sqlite_pool: DatabasePool
    ):
        async with sqlite_pool.connection() as conn:
            await _insert_sqlite_call(conn, "modified-call", "session-modified-sqlite", "2025-01-15T10:00:00")
            await conn.execute(
                """
                INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "modified-request-event",
                "modified-call",
                "transaction.request_recorded",
                json.dumps(
                    {
                        "final_model": DEFAULT_TEST_MODEL,
                        "original_request": {"messages": [{"role": "user", "content": "before"}]},
                        "final_request": {"messages": [{"role": "user", "content": "after"}]},
                    }
                ),
                "session-modified-sqlite",
                "2025-01-15T10:00:00",
            )
            projections = await service._fetch_request_projections(
                conn,
                "session-modified-sqlite",
                [CallEventRange("modified-call", datetime(2025, 1, 15, 10, 0, 0), datetime(2025, 1, 15, 10, 0, 0))],
                is_sqlite=True,
            )

        assert projections[0].request_was_modified is True
        assert service._select_fast_path_anchor(projections) is None

    @pytest.mark.asyncio
    async def test_sqlite_projection_request_param_shape_matches_slow_path(self, sqlite_pool: DatabasePool):
        payload = {
            "final_model": DEFAULT_TEST_MODEL,
            "final_request": {
                "model": DEFAULT_TEST_MODEL,
                "max_tokens": 512,
                "stream": True,
                "temperature": 0.2,
                "top_p": 0.9,
                "top_k": 40,
                "stop_sequences": ["END", "STOP"],
                "output_config": {"format": {"type": "json_schema", "schema": {"private": True}}},
                "tools": [{"name": "lookup"}],
                "messages": [{"role": "user", "content": "hello"}],
            },
        }
        async with sqlite_pool.connection() as conn:
            await _insert_sqlite_call(conn, "param-call", "session-param-sqlite", "2025-01-15T10:00:00")
            await conn.execute(
                """
                INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                "param-shape-event",
                "param-call",
                "transaction.request_recorded",
                json.dumps(payload),
                "session-param-sqlite",
                "2025-01-15T10:00:00",
            )
            projections = await service._fetch_request_projections(
                conn,
                "session-param-sqlite",
                [CallEventRange("param-call", datetime(2025, 1, 15, 10, 0, 0), datetime(2025, 1, 15, 10, 0, 0))],
                is_sqlite=True,
            )

        slow_turn = service._build_turn(
            "param-call",
            [
                StoredEvent(
                    event_type="transaction.request_recorded",
                    payload=payload,
                    created_at=datetime(2025, 1, 15, 10, 0, 0),
                )
            ],
        )
        assert type(projections[0].request_params["stream"]) is bool
        assert type(projections[0].request_params["stop_sequences"]) is list
        assert json.dumps(projections[0].request_params, sort_keys=True) == json.dumps(
            slow_turn.request_params,
            sort_keys=True,
        )

    @pytest.mark.asyncio
    async def test_windowed_detail_defaults_to_newest_page_with_metadata(self, sqlite_pool: DatabasePool):
        rows = _window_rows(6)
        async with sqlite_pool.connection() as conn:
            inserted_call_ids: set[str] = set()
            for row in rows:
                call_id = str(row["call_id"])
                if call_id not in inserted_call_ids:
                    await _insert_sqlite_call(
                        conn,
                        call_id,
                        "session-window-default",
                        parse_db_ts(row["created_at"]).isoformat(),
                    )
                    inserted_call_ids.add(call_id)
                await conn.execute(
                    """
                    INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"default-{row['call_id']}-{row['event_type']}",
                    row["call_id"],
                    row["event_type"],
                    json.dumps(row["payload"]),
                    "session-window-default",
                    parse_db_ts(row["created_at"]).isoformat(),
                )

        detail = await fetch_session_detail("session-window-default", sqlite_pool, offset=None, limit=2)

        assert detail.total_turns == 6
        assert detail.offset == 4
        assert detail.limit == 2
        assert detail.has_more is True
        assert [turn.call_id for turn in detail.turns] == ["window-call-5", "window-call-6"]
        assert [turn.request_messages[0].content for turn in detail.turns] == ["message 5", "message 6"]

    @pytest.mark.asyncio
    async def test_windowed_turns_match_full_slice_for_sqlite_middle_window(self, sqlite_pool: DatabasePool):
        rows = _window_rows(5)
        async with sqlite_pool.connection() as conn:
            inserted_call_ids: set[str] = set()
            for row in rows:
                call_id = str(row["call_id"])
                if call_id not in inserted_call_ids:
                    await _insert_sqlite_call(
                        conn,
                        call_id,
                        "session-window-slice",
                        parse_db_ts(row["created_at"]).isoformat(),
                    )
                    inserted_call_ids.add(call_id)
                await conn.execute(
                    """
                    INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"slice-{row['call_id']}-{row['event_type']}",
                    row["call_id"],
                    row["event_type"],
                    json.dumps(row["payload"]),
                    "session-window-slice",
                    parse_db_ts(row["created_at"]).isoformat(),
                )

        full_ranges = await service._fetch_call_ranges("session-window-slice", sqlite_pool)
        full_turns = [turn async for turn in iter_session_turns("session-window-slice", sqlite_pool, full_ranges)]
        window = await fetch_session_detail("session-window-slice", sqlite_pool, offset=2, limit=2)

        assert [turn.model_dump_json() for turn in window.turns] == [turn.model_dump_json() for turn in full_turns[2:4]]
        assert window.offset == 2
        assert window.limit == 2
        assert window.has_more is True
        assert window.total_turns == 5

    @pytest.mark.asyncio
    async def test_windowed_projection_query_limits_before_json_projection(self):
        rows = _window_rows(3)
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        await fetch_session_detail("session-bounded-query", mock_pool, offset=1, limit=1)

        projection_query = next(
            call.args[0].lower() for call in mock_conn.fetch.call_args_list if "raw_msg_count" in call.args[0].lower()
        )
        source_index = projection_query.index("from \n                (")
        limit_index = projection_query.index("limit $2 offset $3")
        projection_index = projection_query.index("order by call_id, created_at asc")
        assert source_index < limit_index < projection_index

    @pytest.mark.asyncio
    async def test_windowed_detail_stats_match_list_and_stay_invariant_for_sqlite(self, sqlite_pool: DatabasePool):
        rows = _window_rows(6)
        for index, row in enumerate(rows):
            if row["event_type"] != "transaction.request_recorded":
                continue
            payload = row["payload"]
            assert isinstance(payload, dict)
            final_request = payload["final_request"]
            assert isinstance(final_request, dict)
            model = "claude-opus-4-6" if index % 4 == 0 else "gpt-4"
            payload["final_model"] = model
            final_request["model"] = model
        rows.extend(
            [
                {
                    "call_id": "window-call-2",
                    "event_type": "policy.string_replacement.request_modified",
                    "payload": {"summary": "request changed"},
                    "created_at": datetime(2025, 1, 15, 10, 1, 30),
                },
                {
                    "call_id": "window-call-5",
                    "event_type": "policy.judge.tool_call_blocked",
                    "payload": {"summary": "tool blocked"},
                    "created_at": datetime(2025, 1, 15, 10, 4, 30),
                },
                {
                    "call_id": "window-call-6",
                    "event_type": "policy.anthropic_judge.evaluation_complete",
                    "payload": {"summary": "judge bookkeeping"},
                    "created_at": datetime(2025, 1, 15, 10, 5, 30),
                },
            ]
        )
        async with sqlite_pool.connection() as conn:
            await _insert_sqlite_event_rows(conn, "session-window-stats", rows)

        summary = (await fetch_session_list(limit=10, db_pool=sqlite_pool)).sessions[0]
        first_page = await fetch_session_detail("session-window-stats", sqlite_pool, offset=0, limit=2)
        middle_page = await fetch_session_detail("session-window-stats", sqlite_pool, offset=2, limit=2)
        last_page = await fetch_session_detail("session-window-stats", sqlite_pool, offset=4, limit=2)
        streamed_last_page = json.loads(
            (
                await _collect_bytes(stream_session_detail_json("session-window-stats", sqlite_pool, offset=4, limit=2))
            ).decode()
        )

        assert summary.policy_interventions == 2
        assert summary.models_used == ["claude-opus-4-6", "gpt-4"]
        for detail in [first_page, middle_page, last_page]:
            assert detail.total_policy_interventions == summary.policy_interventions
            assert detail.models_used == summary.models_used
        assert streamed_last_page["total_policy_interventions"] == summary.policy_interventions
        assert streamed_last_page["models_used"] == summary.models_used

    @pytest.mark.asyncio
    async def test_windowed_detail_stats_stay_invariant_for_pg_queries(self):
        rows = _window_rows(5)
        for index, row in enumerate(rows):
            if row["event_type"] != "transaction.request_recorded":
                continue
            payload = row["payload"]
            assert isinstance(payload, dict)
            final_request = payload["final_request"]
            assert isinstance(final_request, dict)
            model = "claude-3-opus" if index == 0 else "gpt-4"
            payload["final_model"] = model
            final_request["model"] = model
        rows.append(
            {
                "call_id": "window-call-1",
                "event_type": "policy.string_replacement.response_modified",
                "payload": {"summary": "response changed"},
                "created_at": datetime(2025, 1, 15, 10, 0, 30),
            }
        )
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        first_page = await fetch_session_detail("session-pg-window-stats", mock_pool, offset=0, limit=2)
        last_page = await fetch_session_detail("session-pg-window-stats", mock_pool, offset=3, limit=2)

        assert first_page.total_policy_interventions == 1
        assert last_page.total_policy_interventions == 1
        assert first_page.models_used == ["claude-3-opus", "gpt-4"]
        assert last_page.models_used == ["claude-3-opus", "gpt-4"]

    @pytest.mark.asyncio
    async def test_windowed_detail_large_request_payload_parses_are_bounded_by_window(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        original_json_list = service._json_list

        async def large_parse_count(turn_count: int) -> int:
            parse_count = 0

            def counting_json_list(value):
                nonlocal parse_count
                if isinstance(value, list) and len(value) > 20:
                    parse_count += 1
                return original_json_list(value)

            monkeypatch.setattr(service, "_json_list", counting_json_list)
            rows = _window_rows(turn_count)
            mock_conn = AsyncMock()
            _enable_mock_transaction(mock_conn)
            _stub_event_rows(mock_conn, rows)
            mock_pool = MagicMock()
            mock_pool.is_sqlite = False
            mock_pool.connection.return_value.__aenter__.return_value = mock_conn

            await fetch_session_detail("session-bounded-parse", mock_pool, offset=100, limit=10)
            return parse_count

        assert await large_parse_count(200) == 1
        assert await large_parse_count(400) == 1

    @pytest.mark.asyncio
    async def test_windowed_detail_preserves_modified_request_slow_fallback(self, sqlite_pool: DatabasePool):
        rows = _window_rows(3)
        modified_payload = rows[2]["payload"]
        assert isinstance(modified_payload, dict)
        final_request = modified_payload["final_request"]
        assert isinstance(final_request, dict)
        modified_payload["original_request"] = {"messages": [{"role": "user", "content": "original"}]}
        final_request["messages"] = [*final_request["messages"], {"role": "user", "content": "modified"}]
        async with sqlite_pool.connection() as conn:
            inserted_call_ids: set[str] = set()
            for row in rows:
                call_id = str(row["call_id"])
                if call_id not in inserted_call_ids:
                    await _insert_sqlite_call(
                        conn,
                        call_id,
                        "session-window-modified",
                        parse_db_ts(row["created_at"]).isoformat(),
                    )
                    inserted_call_ids.add(call_id)
                await conn.execute(
                    """
                    INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"modified-{row['call_id']}-{row['event_type']}",
                    row["call_id"],
                    row["event_type"],
                    json.dumps(row["payload"]),
                    "session-window-modified",
                    parse_db_ts(row["created_at"]).isoformat(),
                )

        window = await fetch_session_detail("session-window-modified", sqlite_pool, offset=1, limit=1)

        assert len(window.turns) == 1
        assert window.turns[0].request_was_modified is True
        assert window.turns[0].request_messages_full is not None
        assert window.turns[0].original_request_messages is not None

    @pytest.mark.asyncio
    async def test_stream_session_detail_json_matches_fetch_window(self, sqlite_pool: DatabasePool):
        rows = _window_rows(4)
        async with sqlite_pool.connection() as conn:
            inserted_call_ids: set[str] = set()
            for row in rows:
                call_id = str(row["call_id"])
                if call_id not in inserted_call_ids:
                    await _insert_sqlite_call(
                        conn,
                        call_id,
                        "session-window-stream",
                        parse_db_ts(row["created_at"]).isoformat(),
                    )
                    inserted_call_ids.add(call_id)
                await conn.execute(
                    """
                    INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"stream-{row['call_id']}-{row['event_type']}",
                    row["call_id"],
                    row["event_type"],
                    json.dumps(row["payload"]),
                    "session-window-stream",
                    parse_db_ts(row["created_at"]).isoformat(),
                )

        fetched = await fetch_session_detail("session-window-stream", sqlite_pool, offset=1, limit=2)
        streamed = json.loads(
            (
                await _collect_bytes(
                    stream_session_detail_json("session-window-stream", sqlite_pool, offset=1, limit=2)
                )
            ).decode()
        )

        assert streamed == fetched.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_window_boundary_ignores_prior_preflight_raw_message_count(self):
        rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {"final_request": {"messages": [{"role": "user", "content": "first"}]}},
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "call_id": "preflight",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_request": {
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": f"probe {index}"} for index in range(10)],
                    }
                },
                "created_at": datetime(2025, 1, 15, 10, 1, 0),
            },
            {
                "call_id": "call-2",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_request": {
                        "messages": [
                            {"role": "user", "content": "first"},
                            {"role": "assistant", "content": "ack"},
                            {"role": "user", "content": "second"},
                        ]
                    }
                },
                "created_at": datetime(2025, 1, 15, 10, 2, 0),
            },
        ]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        detail = await fetch_session_detail("session-preflight-boundary", mock_pool, offset=2, limit=1)

        assert [message.content for message in detail.turns[0].request_messages] == ["ack", "second"]

    @pytest.mark.asyncio
    async def test_exports_remain_full_session_when_detail_default_is_windowed(self):
        rows = _window_rows(60)
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        detail_body = json.loads(await _collect_bytes(stream_session_detail_json("session-export-full", mock_pool)))
        jsonl = (await _collect_bytes(service.stream_session_jsonl("session-export-full", mock_pool))).decode()
        markdown = (await _collect_bytes(service.stream_session_markdown("session-export-full", mock_pool))).decode()

        assert len(detail_body["turns"]) == 50
        assert len(jsonl.strip().splitlines()) == 60
        assert "**Turns:** 60" in markdown

    @pytest.mark.asyncio
    async def test_windowed_detail_offset_beyond_end_returns_empty_metadata(self, sqlite_pool: DatabasePool):
        rows = _window_rows(2)
        async with sqlite_pool.connection() as conn:
            inserted_call_ids: set[str] = set()
            for row in rows:
                call_id = str(row["call_id"])
                if call_id not in inserted_call_ids:
                    await _insert_sqlite_call(
                        conn,
                        call_id,
                        "session-window-empty",
                        parse_db_ts(row["created_at"]).isoformat(),
                    )
                    inserted_call_ids.add(call_id)
                await conn.execute(
                    """
                    INSERT INTO conversation_events (id, call_id, event_type, payload, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    f"empty-{row['call_id']}-{row['event_type']}",
                    row["call_id"],
                    row["event_type"],
                    json.dumps(row["payload"]),
                    "session-window-empty",
                    parse_db_ts(row["created_at"]).isoformat(),
                )

        detail = await fetch_session_detail("session-window-empty", sqlite_pool, offset=99, limit=10)

        assert detail.turns == []
        assert detail.total_turns == 2
        assert detail.offset == 99
        assert detail.limit == 10
        assert detail.has_more is True


class TestGetEventSummary:
    """Test friendly-text fallback for known policy event types."""

    @pytest.mark.parametrize(
        "event_type,expected",
        [
            (
                "policy.string_replacement.request_modified",
                "Request modified by string replacement",
            ),
            (
                "policy.string_replacement.response_modified",
                "Response modified by string replacement",
            ),
            ("policy.judge.tool_call_blocked", "Tool call blocked"),
        ],
    )
    def test_falls_back_to_event_type_description(self, event_type, expected):
        """When payload has no `summary`, use the dict-based description."""
        assert _get_event_summary(event_type, None) == expected
        assert _get_event_summary(event_type, {}) == expected
        assert _get_event_summary(event_type, {"summary": ""}) == expected

    def test_payload_summary_takes_precedence(self):
        """A non-empty payload `summary` wins over the fallback dict."""
        assert (
            _get_event_summary(
                "policy.string_replacement.response_modified",
                {"summary": "Replaced 'foo' with 'bar'"},
            )
            == "Replaced 'foo' with 'bar'"
        )

    def test_unknown_event_type_returns_raw(self):
        """Unknown event types fall through to the raw event_type string."""
        assert _get_event_summary("policy.unknown.event", None) == "policy.unknown.event"


class TestExtractTextContent:
    """Test text content extraction from various message formats."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            ("Hello world", "Hello world"),
            ("", ""),
            (None, ""),
            ([{"type": "text", "text": "First"}], "First"),
            ([{"type": "text", "text": "A"}, {"type": "text", "text": "B"}], "A\nB"),
            ([{"type": "image", "url": "http://..."}], ""),
            ([{"type": "text", "text": "Text"}, {"type": "tool_use", "id": "123"}], "Text"),
        ],
    )
    def test_extract_content(self, content, expected):
        """Test extracting content from various formats."""
        assert extract_text_content(content) == expected


class TestExtractPreviewMessage:
    """Test preview message extraction for session list display."""

    def test_basic_message(self):
        """Test extracting a basic user message."""
        payload = {"final_request": {"messages": [{"role": "user", "content": "Hello world"}]}}
        assert _extract_preview_message(payload) == "Hello world"

    def test_multiple_messages_returns_first_user(self):
        """Test that the first user message is returned (captures session intent)."""
        payload = {
            "final_request": {
                "messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "Answer"},
                    {"role": "user", "content": "Follow-up question"},
                ]
            }
        }
        assert _extract_preview_message(payload) == "First question"

    def test_truncates_long_messages(self):
        """Test that long messages are truncated to 100 chars."""
        long_message = "x" * 150
        payload = {"final_request": {"messages": [{"role": "user", "content": long_message}]}}
        result = _extract_preview_message(payload)
        assert result is not None
        assert len(result) == 103  # 100 chars + "..."
        assert result.endswith("...")

    def test_normalizes_whitespace(self):
        """Test that newlines and extra whitespace are collapsed."""
        payload = {"final_request": {"messages": [{"role": "user", "content": "Hello\n\nworld\n  test"}]}}
        assert _extract_preview_message(payload) == "Hello world test"

    def test_inline_system_reminder_matches_summary_preview(self):
        """Inline system reminders are stripped by the shared preview extractor."""
        payload = {
            "final_request": {
                "messages": [{"role": "user", "content": "real question <system-reminder>noise</system-reminder>"}]
            }
        }

        assert _extract_preview_message(payload) == "real question"

    def test_none_payload(self):
        """Test handling of None payload."""
        assert _extract_preview_message(None) is None

    def test_empty_payload(self):
        """Test handling of empty dict payload."""
        assert _extract_preview_message({}) is None

    def test_no_user_messages(self):
        """Test handling when no user messages present."""
        payload = {"final_request": {"messages": [{"role": "system", "content": "System prompt"}]}}
        assert _extract_preview_message(payload) is None

    def test_json_string_payload(self):
        """Test handling of JSON string payload from asyncpg."""
        import json

        payload_dict = {"final_request": {"messages": [{"role": "user", "content": "From JSON"}]}}
        payload_str = json.dumps(payload_dict)
        assert _extract_preview_message(payload_str) == "From JSON"

    def test_prefers_original_request_over_final_request(self):
        """Preview reflects what the user typed, not gateway-injected content.

        When inject_policy_awareness_anthropic (or any future injection) modifies
        the first user message in final_request, the preview must still show the
        user's original text — otherwise every session looks identical.
        """
        payload = {
            "original_request": {"messages": [{"role": "user", "content": "What is the capital of France?"}]},
            "final_request": {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "<policy-context>Your responses may be modified by the following active "
                            "policies before reaching the user: TestPolicy.</policy-context>\n\n"
                            "What is the capital of France?"
                        ),
                    }
                ]
            },
        }
        assert _extract_preview_message(payload) == "What is the capital of France?"

    def test_falls_back_to_final_request_when_original_missing(self):
        """Older payloads (recorded before original_request was stored) still produce a preview."""
        payload = {"final_request": {"messages": [{"role": "user", "content": "Legacy payload"}]}}
        assert _extract_preview_message(payload) == "Legacy payload"

    @pytest.mark.parametrize(
        "probe_content",
        ["count", "quota", "ping", "any-future-probe"],
    )
    def test_skips_probe_requests_with_max_tokens_1(self, probe_content):
        """Test that requests with max_tokens=1 are skipped regardless of content.

        Claude Code sends internal probes (token counting, quota checks) with
        max_tokens=1. This structural signal catches all probes without needing
        a content blocklist.
        """
        payload = {
            "final_request": {
                "max_tokens": 1,
                "messages": [{"role": "user", "content": probe_content}],
            }
        }
        assert _extract_preview_message(payload) is None

    def test_normal_max_tokens_not_skipped(self):
        """Test that requests with normal max_tokens are not skipped."""
        payload = {
            "final_request": {
                "max_tokens": 32000,
                "messages": [{"role": "user", "content": "Hello"}],
            }
        }
        assert _extract_preview_message(payload) == "Hello"

    def test_max_tokens_string_1_skipped(self):
        """Test that max_tokens as string "1" is also caught (JSON parsing)."""
        payload = {
            "final_request": {
                "max_tokens": "1",
                "messages": [{"role": "user", "content": "quota"}],
            }
        }
        assert _extract_preview_message(payload) is None

    def test_missing_max_tokens_not_skipped(self):
        """Test that missing max_tokens doesn't skip (backwards compat)."""
        payload = {
            "final_request": {
                "messages": [{"role": "user", "content": "Hello"}],
            }
        }
        assert _extract_preview_message(payload) == "Hello"


class TestSafeParseJson:
    """Test safe JSON parsing."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ('{"key": "value"}', {"key": "value"}),
            ("{}", {}),
            ('{"nested": {"a": 1}}', {"nested": {"a": 1}}),
            ("invalid", None),
            ("[]", None),  # Not a dict
            ('"string"', None),  # Not a dict
        ],
    )
    def test_parse_json(self, input_str, expected):
        """Test JSON parsing with various inputs."""
        assert _safe_parse_json(input_str) == expected


class TestExtractToolCalls:
    """Test tool call extraction from messages."""

    def test_openai_style_tool_calls(self):
        """Test extracting OpenAI-style tool calls."""
        message = {
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'},
                }
            ]
        }

        result = _extract_tool_calls(message)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_CALL
        assert result[0].tool_name == "read_file"
        assert result[0].tool_call_id == "call_123"
        assert result[0].tool_input == {"path": "/tmp/test"}

    def test_anthropic_style_content_blocks(self):
        """Test extracting Anthropic-style tool_use content blocks."""
        message = {
            "content": [
                {"type": "text", "text": "Let me read that file"},
                {"type": "tool_use", "id": "toolu_123", "name": "Read", "input": {"file": "test.py"}},
            ]
        }

        result = _extract_tool_calls(message)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_CALL
        assert result[0].tool_name == "Read"
        assert result[0].tool_call_id == "toolu_123"
        assert result[0].tool_input == {"file": "test.py"}

    def test_no_tool_calls(self):
        """Test message without tool calls."""
        message = {"content": "Hello world"}
        result = _extract_tool_calls(message)
        assert len(result) == 0

    def test_explicit_none_tool_calls(self):
        """Test message with tool_calls explicitly set to None.

        This case occurs in real OpenAI responses where tool_calls is present
        but null, not just missing from the dict.
        """
        message = {"content": "Hello world", "tool_calls": None}
        result = _extract_tool_calls(message)
        assert len(result) == 0


class TestAnthropicToolResultExtraction:
    """Test Anthropic-style tool_result extraction in user messages.

    When a user message contains a list with tool_result blocks, they are
    extracted into separate TOOL_RESULT messages with tool_call_id (from
    tool_use_id), and text blocks are extracted as separate USER messages.
    """

    def test_single_tool_result_in_user_message(self):
        """Test extracting a single tool_result from a user message."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_abc123", "content": "result text"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == "result text"
        assert result[0].tool_call_id == "toolu_abc123"

    def test_mixed_text_and_tool_result(self):
        """Test extracting mixed text + tool_result blocks from a user message."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Here's what I found"},
                        {"type": "tool_result", "tool_use_id": "toolu_xyz", "content": "Tool output"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 2
        assert result[0].message_type == MessageType.USER
        assert result[0].content == "Here's what I found"
        assert result[1].message_type == MessageType.TOOL_RESULT
        assert result[1].content == "Tool output"
        assert result[1].tool_call_id == "toolu_xyz"

    def test_multiple_tool_result_blocks(self):
        """Test extracting multiple tool_result blocks from a user message."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "First result"},
                        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "Second result"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 2
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == "First result"
        assert result[0].tool_call_id == "toolu_1"
        assert result[1].message_type == MessageType.TOOL_RESULT
        assert result[1].content == "Second result"
        assert result[1].tool_call_id == "toolu_2"

    def test_tool_result_with_none_content(self):
        """Test that tool_result with None content becomes empty string."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_empty", "content": None},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == ""
        assert result[0].tool_call_id == "toolu_empty"

    def test_tool_result_with_string_content(self):
        """Test that tool_result with string content (not list) is extracted."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_str", "content": "Plain string result"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == "Plain string result"
        assert result[0].tool_call_id == "toolu_str"

    def test_tool_result_missing_tool_use_id(self):
        """Test that tool_result without tool_use_id gets tool_call_id=None.

        The frontend cannot pair such results with tool calls, but parsing
        must not crash.
        """
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": "orphan result"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == "orphan result"
        assert result[0].tool_call_id is None

    def test_tool_result_with_nested_content_blocks(self):
        """Test tool_result with nested content blocks (list of text blocks)."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_nested",
                            "content": [
                                {"type": "text", "text": "First part"},
                                {"type": "text", "text": "Second part"},
                            ],
                        },
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        # Nested content blocks should be concatenated with newlines
        assert result[0].content == "First part\nSecond part"
        assert result[0].tool_call_id == "toolu_nested"

    def test_text_only_user_message_not_special_path(self):
        """Test that text-only user message (no tool_results) uses normal path."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Just a question"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.USER
        assert result[0].content == "Just a question"
        assert result[0].tool_call_id is None

    def test_whitespace_only_text_blocks_filtered(self):
        """Test that whitespace-only text blocks are filtered out."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "   \n\t  "},  # Whitespace only
                        {"type": "tool_result", "tool_use_id": "toolu_ws", "content": "Result"},
                        {"type": "text", "text": "Valid text"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 2
        # Whitespace-only text should be filtered
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == "Result"
        assert result[1].message_type == MessageType.USER
        assert result[1].content == "Valid text"

    def test_tool_result_with_is_error_true(self):
        """Test that tool_result with is_error=True is propagated."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_error",
                            "content": "Error occurred",
                            "is_error": True,
                        },
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].content == "Error occurred"
        assert result[0].tool_call_id == "toolu_error"
        assert result[0].is_error is True

    def test_tool_result_with_is_error_false(self):
        """Test that is_error=False is normalized to None (no error badge)."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_ok",
                            "content": "Success",
                            "is_error": False,
                        },
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].is_error is None

    def test_complex_mixed_blocks(self):
        """Test a complex scenario with multiple text + tool_result blocks."""
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "First observation"},
                        {"type": "tool_result", "tool_use_id": "tool1", "content": "Data from tool 1"},
                        {"type": "text", "text": "Second observation"},
                        {"type": "tool_result", "tool_use_id": "tool2", "content": "Data from tool 2"},
                    ],
                }
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 4
        assert result[0].message_type == MessageType.USER
        assert result[0].content == "First observation"
        assert result[1].message_type == MessageType.TOOL_RESULT
        assert result[1].tool_call_id == "tool1"
        assert result[2].message_type == MessageType.USER
        assert result[2].content == "Second observation"
        assert result[3].message_type == MessageType.TOOL_RESULT
        assert result[3].tool_call_id == "tool2"


class TestParseRequestMessages:
    """Test request message parsing."""

    def test_simple_messages(self):
        """Test parsing simple text messages."""
        request = {
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 2
        assert result[0].message_type == MessageType.SYSTEM
        assert result[0].content == "You are helpful"
        assert result[1].message_type == MessageType.USER
        assert result[1].content == "Hello"

    def test_unrecognized_role_raises_error(self):
        """Test that unrecognized message roles raise ValueError."""
        request = {
            "messages": [
                {"role": "unknown_role", "content": "Hello"},
            ]
        }

        with pytest.raises(ValueError, match="Unrecognized message role: 'unknown_role'"):
            _parse_request_messages(request)

    def test_assistant_message_with_tool_calls(self):
        """Test parsing assistant messages with tool_calls in request.

        When conversation history includes an assistant message that made
        tool calls, those tool calls must be extracted and included.
        """
        request = {
            "messages": [
                {"role": "user", "content": "What's the weather in Tokyo?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"location": "Tokyo"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_abc123",
                    "content": '{"temperature": 22, "conditions": "sunny"}',
                },
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 3
        # First: user message
        assert result[0].message_type == MessageType.USER
        assert "weather" in result[0].content.lower()
        # Second: tool call from assistant
        assert result[1].message_type == MessageType.TOOL_CALL
        assert result[1].tool_name == "get_weather"
        assert result[1].tool_call_id == "call_abc123"
        # Third: tool result
        assert result[2].message_type == MessageType.TOOL_RESULT
        assert result[2].tool_call_id == "call_abc123"

    def test_tool_result_message(self):
        """Test parsing tool result messages."""
        request = {
            "messages": [
                {"role": "tool", "content": "File contents...", "tool_call_id": "call_123"},
            ]
        }

        result = _parse_request_messages(request)

        assert len(result) == 1
        assert result[0].message_type == MessageType.TOOL_RESULT
        assert result[0].tool_call_id == "call_123"


class TestParseResponseMessages:
    """Test response message parsing."""

    def test_simple_response(self):
        """Test parsing simple text response."""
        response = {"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]}

        result = _parse_response_messages(response)

        assert len(result) == 1
        assert result[0].message_type == MessageType.ASSISTANT
        assert result[0].content == "Hello!"

    def test_response_with_tool_calls(self):
        """Test parsing response with tool calls."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Let me check",
                        "tool_calls": [{"id": "call_1", "function": {"name": "read", "arguments": "{}"}}],
                    }
                }
            ]
        }

        result = _parse_response_messages(response)

        assert len(result) == 2
        assert result[0].message_type == MessageType.ASSISTANT
        assert result[1].message_type == MessageType.TOOL_CALL

    def test_anthropic_text_response(self):
        """Test parsing Anthropic-format response with text content."""
        response = {
            "id": "msg_test123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello from Claude!"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
        }

        result = _parse_response_messages(response)

        assert len(result) == 1
        assert result[0].message_type == MessageType.ASSISTANT
        assert result[0].content == "Hello from Claude!"

    def test_anthropic_response_with_tool_use(self):
        """Test parsing Anthropic-format response with tool_use content blocks."""
        response = {
            "id": "msg_test456",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me read that file."},
                {"type": "tool_use", "id": "toolu_123", "name": "read_file", "input": {"path": "/foo.py"}},
            ],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
        }

        result = _parse_response_messages(response)

        assert len(result) == 2
        assert result[0].message_type == MessageType.ASSISTANT
        assert result[0].content == "Let me read that file."
        assert result[1].message_type == MessageType.TOOL_CALL
        assert result[1].tool_name == "read_file"
        assert result[1].tool_call_id == "toolu_123"

    def test_anthropic_empty_text_response(self):
        """Test parsing Anthropic response with empty content blocks."""
        response = {
            "id": "msg_test789",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
        }

        result = _parse_response_messages(response)

        assert len(result) == 0


class TestBuildTurn:
    """Test building conversation turns from events."""

    def test_simple_turn(self):
        """Test building a simple request/response turn."""
        events: list[StoredEvent] = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Hello"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "event_type": "transaction.streaming_response_recorded",
                "payload": {
                    "original_response": {"choices": [{"message": {"content": "Hi!"}}]},
                    "final_response": {"choices": [{"message": {"content": "Hi!"}}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        turn = _build_turn("call-123", events)

        assert turn.call_id == "call-123"
        assert turn.model == "gpt-4"
        assert len(turn.request_messages) == 1
        assert len(turn.response_messages) == 1
        assert not turn.had_policy_intervention

    def test_request_params_allowlist(self):
        """Test that request_params only includes allowlisted fields."""
        events: list[StoredEvent] = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 1024,
                        "model": "gpt-4",
                        "stream": True,
                        "metadata": {"api_key": "secret"},
                        "system": "You are helpful",
                        "tools": [{"name": "tool1"}, {"name": "tool2"}],
                    },
                    "final_request": {
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 1024,
                        "model": "gpt-4",
                        "stream": True,
                        "metadata": {"api_key": "secret"},
                        "system": "You are helpful",
                        "tools": [{"name": "tool1"}, {"name": "tool2"}],
                    },
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
        ]

        turn = _build_turn("call-123", events)

        assert turn.request_params is not None
        assert turn.request_params["max_tokens"] == 1024
        assert turn.request_params["model"] == "gpt-4"
        assert turn.request_params["stream"] is True
        assert turn.request_params["tools_count"] == 2
        # Sensitive/unknown fields must NOT leak
        assert "metadata" not in turn.request_params
        assert "system" not in turn.request_params
        assert "messages" not in turn.request_params
        assert "tools" not in turn.request_params

    def test_turn_with_policy_intervention(self):
        """Test turn with policy modification."""
        events: list[StoredEvent] = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Original"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Modified"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "event_type": "policy.judge.tool_call_blocked",
                "payload": {"summary": "Tool call blocked for safety"},
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
        ]

        turn = _build_turn("call-123", events)

        assert turn.had_policy_intervention
        assert turn.request_was_modified
        assert turn.original_request_messages is not None
        assert turn.original_request_messages[0].content == "Original"
        assert len(turn.annotations) == 1
        assert turn.annotations[0].policy_name == "judge"

    def test_missing_final_request_raises_error(self):
        """Test that missing final_request raises KeyError."""
        events: list[StoredEvent] = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Hello"}]},
                    # final_request is missing
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
        ]

        with pytest.raises(KeyError, match="final_request"):
            _build_turn("call-123", events)

    def test_missing_final_response_raises_error(self):
        """Test that missing final_response raises KeyError."""
        events: list[StoredEvent] = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Hello"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Hello"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "event_type": "transaction.streaming_response_recorded",
                "payload": {
                    "original_response": {"choices": [{"message": {"content": "Hi!"}}]},
                    # final_response is missing
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        with pytest.raises(KeyError, match="final_response"):
            _build_turn("call-123", events)

    def test_anthropic_turn_with_text_response(self):
        """Test building a turn from Anthropic-format request and response events."""
        events: list[StoredEvent] = [
            {
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": DEFAULT_TEST_MODEL,
                    "original_request": {
                        "model": DEFAULT_TEST_MODEL,
                        "messages": [{"role": "user", "content": "Hello Claude"}],
                        "max_tokens": 1024,
                    },
                    "final_request": {
                        "model": DEFAULT_TEST_MODEL,
                        "messages": [{"role": "user", "content": "Hello Claude"}],
                        "max_tokens": 1024,
                    },
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "event_type": "transaction.non_streaming_response_recorded",
                "payload": {
                    "original_response": {
                        "id": "msg_123",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Hello! How can I help?"}],
                        "model": DEFAULT_TEST_MODEL,
                        "stop_reason": "end_turn",
                    },
                    "final_response": {
                        "id": "msg_123",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Hello! How can I help?"}],
                        "model": DEFAULT_TEST_MODEL,
                        "stop_reason": "end_turn",
                    },
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        turn = _build_turn("call-456", events)

        assert turn.call_id == "call-456"
        assert turn.model == DEFAULT_TEST_MODEL
        assert len(turn.request_messages) == 1
        assert turn.request_messages[0].content == "Hello Claude"
        assert len(turn.response_messages) == 1
        assert turn.response_messages[0].content == "Hello! How can I help?"
        assert turn.response_messages[0].message_type == MessageType.ASSISTANT
        assert not turn.had_policy_intervention


class TestFetchSessionList:
    """Test fetching session list from database."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful session list fetching."""
        mock_rows = [
            {
                "session_id": "session-1",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 10,
                "turn_count": 3,
                "policy_interventions": 1,
                "models_used": "gpt-4,claude-3",
                "preview_message": "Hello world",
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1  # Total count
        # First fetch() = main session aggregation; second = user_ids lookup.
        mock_conn.fetch.side_effect = [mock_rows, []]

        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        assert result.total == 1
        assert result.offset == 0
        assert result.has_more is False
        assert len(result.sessions) == 1
        assert result.sessions[0].session_id == "session-1"
        assert result.sessions[0].turn_count == 3
        assert result.sessions[0].policy_interventions == 1
        assert "gpt-4" in result.sessions[0].models_used
        assert result.sessions[0].preview_message == "Hello world"
        assert result.sessions[0].user_ids == []

    @pytest.mark.asyncio
    async def test_fetch_with_offset(self):
        """Test fetching with offset for pagination."""
        mock_rows = [
            {
                "session_id": "session-2",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 5,
                "turn_count": 2,
                "policy_interventions": 0,
                "models_used": "gpt-4",
                "preview_message": None,
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 100  # Total count
        mock_conn.fetch.side_effect = [mock_rows, []]

        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool, offset=50)

        assert result.total == 100
        assert result.offset == 50
        assert result.has_more is True  # 50 + 1 < 100
        assert len(result.sessions) == 1
        assert result.sessions[0].preview_message is None

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """Test when no sessions found."""
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 0  # Total count
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        assert result.total == 0
        assert result.offset == 0
        assert result.has_more is False
        assert result.sessions == []

    @pytest.mark.asyncio
    async def test_unfiltered_pg_list_uses_session_summaries_without_payload(self):
        """Unfiltered list hot path reads session_summaries and never selects full payloads."""
        summary_rows = [
            {
                "session_id": "session-1",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 10,
                "turn_count": 3,
                "policy_interventions": 1,
                "models_used": "gpt-4,claude-3",
                "preview_message": "Hello world",
            },
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1
        mock_conn.fetch.side_effect = [summary_rows, []]
        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        queries = [call.args[0].lower() for call in mock_conn.fetch.call_args_list]
        assert result.sessions[0].preview_message == "Hello world"
        assert result.sessions[0].models_used == ["claude-3", "gpt-4"]
        assert "from session_summaries" in queries[0]
        assert "payload" not in queries[0]
        assert "request_payload" not in queries[0]

    @pytest.mark.asyncio
    async def test_pg_summary_null_preview_without_payload_returns_none(self):
        """PG summary rows with NULL preview return None without payload fallback."""
        summary_rows = [
            {
                "session_id": "session-null-preview",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 1,
                "turn_count": 1,
                "policy_interventions": 0,
                "models_used": "gpt-4",
                "preview_message": None,
            },
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1
        mock_conn.fetch.side_effect = [summary_rows, []]
        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        assert result.sessions[0].preview_message is None

    @pytest.mark.asyncio
    async def test_pg_summary_models_are_sorted_like_aggregation(self):
        """Summary models are sorted to match existing list output."""
        summary_rows = [
            {
                "session_id": "session-model-order",
                "first_ts": datetime(2025, 1, 15, 10, 0, 0),
                "last_ts": datetime(2025, 1, 15, 11, 0, 0),
                "total_events": 2,
                "turn_count": 2,
                "policy_interventions": 0,
                "models_used": "z-model,a-model",
                "preview_message": "Hello",
            },
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1
        mock_conn.fetch.side_effect = [summary_rows, []]
        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_list(limit=10, db_pool=mock_pool)

        assert result.sessions[0].models_used == ["a-model", "z-model"]

    @pytest.mark.asyncio
    async def test_pg_search_path_still_uses_existing_aggregation(self):
        """Full-text search remains on conversation_events aggregation."""
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 0
        mock_conn.fetch.return_value = []
        mock_pool = MagicMock()
        mock_pool.is_sqlite = False
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        await fetch_session_list(limit=10, db_pool=mock_pool, search=SessionSearchParams(model="gpt-4"))

        query = mock_conn.fetch.call_args.args[0].lower()
        assert "from conversation_events" in query
        assert "request_payload" in query


class TestFetchSessionDetail:
    """Test fetching session detail from database."""

    @pytest.mark.asyncio
    async def test_iter_session_turns_emits_request_message_deltas_with_preflight_excluded_from_count(self):
        """Detail turns carry display-equivalent request deltas instead of cumulative history."""
        ranges = [
            CallEventRange(
                call_id="call-1",
                first_ts=datetime(2025, 1, 15, 10, 0, 0),
                last_ts=datetime(2025, 1, 15, 10, 0, 0),
            ),
            CallEventRange(
                call_id="preflight",
                first_ts=datetime(2025, 1, 15, 10, 0, 30),
                last_ts=datetime(2025, 1, 15, 10, 0, 30),
            ),
            CallEventRange(
                call_id="call-2",
                first_ts=datetime(2025, 1, 15, 10, 1, 0),
                last_ts=datetime(2025, 1, 15, 10, 1, 0),
            ),
        ]
        rows_by_call = {
            "call-1": [
                {
                    "call_id": "call-1",
                    "event_type": "transaction.request_recorded",
                    "payload": {"final_request": {"messages": [{"role": "user", "content": "Hi"}]}},
                    "created_at": ranges[0].first_ts,
                }
            ],
            "preflight": [
                {
                    "call_id": "preflight",
                    "event_type": "transaction.request_recorded",
                    "payload": {
                        "final_request": {
                            "max_tokens": 1,
                            "messages": [{"role": "user", "content": "quota probe"}],
                        }
                    },
                    "created_at": ranges[1].first_ts,
                }
            ],
            "call-2": [
                {
                    "call_id": "call-2",
                    "event_type": "transaction.request_recorded",
                    "payload": {
                        "original_request": {
                            "messages": [
                                {"role": "user", "content": "Hi"},
                                {"role": "assistant", "content": "Hello!"},
                                {"role": "user", "content": "Use forbidden tool"},
                            ]
                        },
                        "final_request": {
                            "messages": [
                                {"role": "user", "content": "Hi"},
                                {"role": "assistant", "content": "Hello!"},
                                {"role": "user", "content": "Use safe tool"},
                            ]
                        },
                    },
                    "created_at": ranges[2].first_ts,
                }
            ],
        }
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)

        _stub_event_rows(mock_conn, [row for call_rows in rows_by_call.values() for row in call_rows])
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        turns = [turn async for turn in iter_session_turns("session-1", mock_pool, ranges)]

        request_contents = [[message.content for message in turn.request_messages] for turn in turns]
        original_contents = [message.content for message in turns[2].original_request_messages or []]
        final_full_contents = [message.content for message in turns[2].request_messages_full or []]
        assert request_contents == [["Hi"], ["quota probe"], ["Hello!", "Use safe tool"]]
        assert original_contents == ["Hi", "Hello!", "Use forbidden tool"]
        assert final_full_contents == ["Hi", "Hello!", "Use safe tool"]
        assert request_contents[0] + request_contents[2] == final_full_contents

    @pytest.mark.asyncio
    async def test_iter_session_turns_fetches_payloads_in_batches(self):
        """Payload fetch query count grows by batch count, not by turn count."""
        ranges = [
            CallEventRange(
                call_id=f"call-{index}",
                first_ts=datetime(2025, 1, 15, 10, index, 0),
                last_ts=datetime(2025, 1, 15, 10, index, 1),
            )
            for index in range(51)
        ]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)

        rows = [
            {
                "call_id": call_id,
                "event_type": "transaction.request_recorded",
                "payload": {"final_request": {"messages": [{"role": "user", "content": call_id}]}},
                "created_at": ranges[int(call_id.split("-")[1])].first_ts,
            }
            for call_range in ranges
            for call_id in [call_range.call_id]
        ]

        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        turns = [turn async for turn in iter_session_turns("session-1", mock_pool, ranges)]

        assert len(turns) == 51
        assert mock_conn.fetch.await_count == 3
        assert all("call_id = any($2)" in call.args[0].lower() for call in mock_conn.fetch.call_args_list)

    @pytest.mark.asyncio
    async def test_iter_session_turns_fetches_sqlite_payload_batches_with_expanded_in_clause(self):
        ranges = [
            CallEventRange(
                call_id="call-1",
                first_ts=datetime(2025, 1, 15, 10, 0, 0),
                last_ts=datetime(2025, 1, 15, 10, 0, 10),
            ),
            CallEventRange(
                call_id="call-2",
                first_ts=datetime(2025, 1, 15, 10, 1, 0),
                last_ts=datetime(2025, 1, 15, 10, 1, 10),
            ),
            CallEventRange(
                call_id="call-3",
                first_ts=datetime(2025, 1, 15, 10, 2, 0),
                last_ts=datetime(2025, 1, 15, 10, 2, 10),
            ),
        ]
        rows_by_call = {
            "call-1": [
                {
                    "call_id": "call-1",
                    "event_type": "transaction.request_recorded",
                    "payload": {"final_request": {"messages": [{"role": "user", "content": "first"}]}},
                    "created_at": ranges[0].first_ts,
                }
            ],
            "call-2": [
                {
                    "call_id": "call-2",
                    "event_type": "transaction.request_recorded",
                    "payload": {
                        "final_request": {
                            "messages": [
                                {"role": "user", "content": "first"},
                                {"role": "user", "content": "second"},
                            ]
                        }
                    },
                    "created_at": ranges[1].first_ts,
                },
                {
                    "call_id": "call-2",
                    "event_type": "transaction.request_recorded",
                    "payload": {
                        "final_request": {
                            "messages": [
                                {"role": "user", "content": "first"},
                                {"role": "user", "content": "late"},
                            ]
                        }
                    },
                    "created_at": datetime(2025, 1, 15, 10, 1, 30),
                },
            ],
            "call-3": [
                {
                    "call_id": "call-3",
                    "event_type": "transaction.request_recorded",
                    "payload": {
                        "final_request": {
                            "messages": [
                                {"role": "user", "content": "first"},
                                {"role": "user", "content": "second"},
                                {"role": "user", "content": "third"},
                            ]
                        }
                    },
                    "created_at": ranges[2].first_ts,
                }
            ],
        }
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)

        _stub_event_rows(mock_conn, [row for call_rows in rows_by_call.values() for row in call_rows])
        mock_pool = MagicMock()
        mock_pool.is_sqlite = True
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        turns = [turn async for turn in iter_session_turns("session-1", mock_pool, ranges)]

        query, session_id, *call_ids = mock_conn.fetch.await_args.args
        assert [turn.call_id for turn in turns] == ["call-1", "call-2", "call-3"]
        assert [[message.content for message in turn.request_messages] for turn in turns] == [
            ["first"],
            ["second"],
            ["third"],
        ]
        assert "call_id IN ($2, $3, $4)" in query
        assert session_id == "session-1"
        assert call_ids == ["call-1", "call-2", "call-3"]

    @pytest.mark.asyncio
    async def test_iter_session_turns_bounds_reads_to_snapshot_last_timestamp(self):
        """Per-call streaming reads are bounded by the enumerated snapshot."""
        first_ts = datetime(2025, 1, 15, 10, 0, 0)
        snapshot_last = datetime(2025, 1, 15, 10, 1, 0)
        ranges = [CallEventRange(call_id="call-1", first_ts=first_ts, last_ts=snapshot_last)]
        rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {"final_request": {"messages": [{"role": "user", "content": "first"}]}},
                "created_at": first_ts,
            },
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {"final_request": {"messages": [{"role": "user", "content": "late"}]}},
                "created_at": datetime(2025, 1, 15, 10, 2, 0),
            },
        ]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        turns = [turn async for turn in iter_session_turns("session-1", mock_pool, ranges)]

        query, session_id, call_ids = mock_conn.fetch.await_args.args
        assert len(turns) == 1
        assert [message.content for message in turns[0].request_messages] == ["first"]
        assert "call_id = ANY($2)" in query
        assert session_id == "session-1"
        assert call_ids == ["call-1"]

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful session detail fetching."""
        mock_rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "original_request": {"messages": [{"role": "user", "content": "Hi"}]},
                    "final_request": {"messages": [{"role": "user", "content": "Hi"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "call_id": "call-1",
                "event_type": "transaction.streaming_response_recorded",
                "payload": {
                    "original_response": {"choices": [{"message": {"content": "Hello!"}}]},
                    "final_response": {"choices": [{"message": {"content": "Hello!"}}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 1),
            },
        ]

        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, mock_rows)

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_detail("session-1", mock_pool)

        assert result.session_id == "session-1"
        assert len(result.turns) == 1
        assert result.turns[0].model == "gpt-4"

    @pytest.mark.asyncio
    async def test_fetch_session_detail_emits_multi_turn_request_message_deltas(self):
        rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_request": {"messages": [{"role": "user", "content": "Plan trip"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "call_id": "preflight",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_request": {
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "quota probe"}],
                    }
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 30),
            },
            {
                "call_id": "call-2",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_request": {
                        "messages": [
                            {"role": "user", "content": "Plan trip"},
                            {"role": "assistant", "content": "Where to?"},
                            {"role": "user", "content": "Lisbon"},
                        ]
                    }
                },
                "created_at": datetime(2025, 1, 15, 10, 1, 0),
            },
            {
                "call_id": "call-3",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_request": {
                        "messages": [
                            {"role": "user", "content": "Plan trip"},
                            {"role": "assistant", "content": "Where to?"},
                            {"role": "user", "content": "Lisbon"},
                            {"role": "assistant", "content": "Which dates?"},
                            {"role": "user", "content": "May"},
                        ]
                    }
                },
                "created_at": datetime(2025, 1, 15, 10, 2, 0),
            },
        ]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_detail("session-1", mock_pool)

        assert [turn.call_id for turn in result.turns] == ["call-1", "preflight", "call-2", "call-3"]
        assert [[message.content for message in turn.request_messages] for turn in result.turns] == [
            ["Plan trip"],
            ["quota probe"],
            ["Where to?", "Lisbon"],
            ["Which dates?", "May"],
        ]

    @pytest.mark.asyncio
    async def test_no_events_found(self):
        """Test error when no events found."""
        mock_conn = AsyncMock()
        _stub_event_rows(mock_conn, [])

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(ValueError, match="No events found"):
            await fetch_session_detail("nonexistent", mock_pool)

    @pytest.mark.asyncio
    async def test_unexpected_payload_type_raises_error(self):
        """Test that unexpected payload type raises TypeError."""
        mock_rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": 12345,  # Unexpected type (not dict or str)
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
        ]

        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, mock_rows)

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(TypeError, match="Unexpected payload type: int"):
            await fetch_session_detail("session-1", mock_pool)

    @pytest.mark.asyncio
    async def test_string_created_at_is_parsed(self):
        """Test that string created_at (from SQLite) is parsed into datetime."""
        mock_rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {"final_model": "gpt-4", "final_request": {"messages": []}},
                "created_at": "2025-01-15T10:00:00",
            },
        ]

        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, mock_rows)

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_session_detail("session-1", mock_pool)
        assert result.first_timestamp == "2025-01-15T10:00:00"

    @pytest.mark.asyncio
    async def test_unexpected_created_at_type_raises_error(self):
        """Test that unexpected created_at type raises TypeError."""
        mock_rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {"final_model": "gpt-4", "final_request": {"messages": []}},
                "created_at": 12345,  # Unexpected type (not datetime or str)
            },
        ]

        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, mock_rows)

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(TypeError, match="got int"):
            await fetch_session_detail("session-1", mock_pool)

    @pytest.mark.asyncio
    async def test_streamed_session_detail_json_matches_existing_fetch_output(self):
        """Streaming detail JSON is semantically identical to existing SessionDetail."""
        from luthien_proxy.history import service

        rows = _equivalence_rows()
        request_payload = rows[2]["payload"]
        assert isinstance(request_payload, dict)
        request_payload["original_request"] = request_payload["final_request"]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn
        expected = await fetch_session_detail("session-1", mock_pool)

        body = await _collect_bytes(service.stream_session_detail_json("session-1", mock_pool))

        assert json.loads(body) == expected.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_streamed_detail_payload_fetches_are_scoped_to_one_call(self):
        """Streaming detail enumerates call ids without payload and fetches payloads per call."""
        from luthien_proxy.history import service

        rows = _equivalence_rows()
        request_payload = rows[2]["payload"]
        assert isinstance(request_payload, dict)
        request_payload["original_request"] = request_payload["final_request"]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        await _collect_bytes(service.stream_session_detail_json("session-1", mock_pool))

        queries = [call.args[0].lower() for call in mock_conn.fetch.call_args_list]
        assert "payload" not in queries[0]
        message_fetch_index = next(index for index, query in enumerate(queries) if " as messages" in query)
        payload_call = mock_conn.fetch.call_args_list[message_fetch_index]
        assert payload_call.args[1] == "session-1"
        assert payload_call.args[2] == ["call-2"]

    @pytest.mark.asyncio
    async def test_streamed_exports_match_existing_export_output(self):
        """Streaming markdown and JSONL exports equal existing exporters."""
        from luthien_proxy.history import service

        rows = _equivalence_rows()
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn
        expected = await fetch_session_detail("session-1", mock_pool)

        markdown = (await _collect_bytes(service.stream_session_markdown("session-1", mock_pool))).decode()
        jsonl = (await _collect_bytes(service.stream_session_jsonl("session-1", mock_pool))).decode()

        assert markdown == export_session_markdown(expected)
        assert jsonl == export_session_jsonl(expected)

    @pytest.mark.asyncio
    async def test_streamed_detail_uses_global_last_timestamp_for_interleaved_calls(self):
        """Streaming detail last timestamp matches global max event timestamp."""
        from luthien_proxy.history import service

        rows = [
            {
                "call_id": "call-1",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "gpt-4",
                    "final_request": {"messages": [{"role": "user", "content": "first"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 0, 0),
            },
            {
                "call_id": "call-2",
                "event_type": "transaction.request_recorded",
                "payload": {
                    "final_model": "claude-3",
                    "final_request": {"messages": [{"role": "user", "content": "second"}]},
                },
                "created_at": datetime(2025, 1, 15, 10, 1, 0),
            },
            {
                "call_id": "call-2",
                "event_type": "transaction.streaming_response_recorded",
                "payload": {"final_response": {"choices": [{"message": {"content": "second done"}}]}},
                "created_at": datetime(2025, 1, 15, 10, 2, 0),
            },
            {
                "call_id": "call-1",
                "event_type": "transaction.streaming_response_recorded",
                "payload": {"final_response": {"choices": [{"message": {"content": "first done"}}]}},
                "created_at": datetime(2025, 1, 15, 10, 3, 0),
            },
        ]
        mock_conn = AsyncMock()
        _enable_mock_transaction(mock_conn)
        _stub_event_rows(mock_conn, rows)
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn
        expected = await fetch_session_detail("session-1", mock_pool)

        detail = json.loads(await _collect_bytes(service.stream_session_detail_json("session-1", mock_pool)))
        markdown = (await _collect_bytes(service.stream_session_markdown("session-1", mock_pool))).decode()

        assert detail["last_timestamp"] == expected.last_timestamp == "2025-01-15T10:03:00"
        assert "**Ended:** 2025-01-15T10:03:00" in markdown


class TestExportSessionMarkdown:
    """Test markdown export functionality."""

    def test_basic_export(self):
        """Test basic markdown export."""
        session = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[ConversationMessage(message_type=MessageType.USER, content="Hello")],
                    response_messages=[ConversationMessage(message_type=MessageType.ASSISTANT, content="Hi there!")],
                    annotations=[],
                    had_policy_intervention=False,
                )
            ],
            total_policy_interventions=0,
            models_used=["gpt-4"],
        )

        markdown = export_session_markdown(session)

        assert "# Conversation History: test-session" in markdown
        assert "## Turn 1" in markdown
        assert "### User" in markdown
        assert "Hello" in markdown
        assert "### Assistant" in markdown
        assert "Hi there!" in markdown

    def test_export_with_tool_call(self):
        """Test markdown export with tool calls."""
        session = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[],
                    response_messages=[
                        ConversationMessage(
                            message_type=MessageType.TOOL_CALL,
                            content="{}",
                            tool_name="read_file",
                            tool_input={"path": "/tmp/test"},
                        )
                    ],
                    annotations=[],
                    had_policy_intervention=False,
                )
            ],
            total_policy_interventions=0,
            models_used=["gpt-4"],
        )

        markdown = export_session_markdown(session)

        assert "### Tool Call" in markdown
        assert "`read_file`" in markdown
        assert '"/tmp/test"' in markdown

    def test_export_with_policy_annotations(self):
        """Test markdown export with policy annotations."""
        session = SessionDetail(
            session_id="test-session",
            first_timestamp="2025-01-15T10:00:00",
            last_timestamp="2025-01-15T11:00:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2025-01-15T10:00:00",
                    model="gpt-4",
                    request_messages=[],
                    response_messages=[],
                    annotations=[
                        PolicyAnnotation(
                            policy_name="judge",
                            event_type="policy.judge.tool_call_blocked",
                            summary="Dangerous operation blocked",
                        )
                    ],
                    had_policy_intervention=True,
                )
            ],
            total_policy_interventions=1,
            models_used=["gpt-4"],
        )

        markdown = export_session_markdown(session)

        assert "### Policy Annotations" in markdown
        assert "**judge**" in markdown
        assert "Dangerous operation blocked" in markdown
        assert "**Policy Interventions:** 1" in markdown


class TestExportSessionJsonl:
    def test_exports_turns_as_jsonl(self):
        session = SessionDetail(
            session_id="sess-1",
            first_timestamp="2026-03-31T10:00:00",
            last_timestamp="2026-03-31T10:01:00",
            turns=[
                ConversationTurn(
                    call_id="call-1",
                    timestamp="2026-03-31T10:00:00",
                    model="claude-3-opus",
                    request_messages=[
                        ConversationMessage(message_type=MessageType.USER, content="Hello"),
                    ],
                    response_messages=[
                        ConversationMessage(message_type=MessageType.ASSISTANT, content="Hi"),
                    ],
                    annotations=[],
                ),
                ConversationTurn(
                    call_id="call-2",
                    timestamp="2026-03-31T10:00:30",
                    model="claude-3-opus",
                    request_messages=[
                        ConversationMessage(message_type=MessageType.USER, content="Help"),
                    ],
                    response_messages=[
                        ConversationMessage(
                            message_type=MessageType.TOOL_CALL,
                            content="{}",
                            tool_name="read_file",
                            tool_call_id="tc-1",
                            tool_input={"path": "/tmp/x"},
                        ),
                    ],
                    annotations=[],
                ),
            ],
            total_policy_interventions=0,
            models_used=["claude-3-opus"],
        )

        result = export_session_jsonl(session)
        lines = result.strip().split("\n")
        assert len(lines) == 2

        line1 = json.loads(lines[0])
        assert line1["call_id"] == "call-1"
        assert line1["session_id"] == "sess-1"
        assert line1["model"] == "claude-3-opus"
        assert len(line1["request_messages"]) == 1
        assert len(line1["response_messages"]) == 1

        line2 = json.loads(lines[1])
        assert line2["call_id"] == "call-2"
        assert line2["response_messages"][0]["tool_name"] == "read_file"

    def test_empty_session_returns_empty_string(self):
        session = SessionDetail(
            session_id="sess-empty",
            first_timestamp="2026-03-31T10:00:00",
            last_timestamp="2026-03-31T10:00:00",
            turns=[],
            total_policy_interventions=0,
            models_used=[],
        )
        result = export_session_jsonl(session)
        assert result == ""

    def test_includes_original_messages_when_modified(self):
        session = SessionDetail(
            session_id="sess-mod",
            first_timestamp="2026-03-31T10:00:00",
            last_timestamp="2026-03-31T10:01:00",
            turns=[
                ConversationTurn(
                    call_id="call-mod",
                    timestamp="2026-03-31T10:00:00",
                    model="claude-3-opus",
                    request_messages=[
                        ConversationMessage(message_type=MessageType.USER, content="Hello"),
                    ],
                    response_messages=[
                        ConversationMessage(message_type=MessageType.ASSISTANT, content="[Link]\n\nHi"),
                    ],
                    original_response_messages=[
                        ConversationMessage(message_type=MessageType.ASSISTANT, content="Hi"),
                    ],
                    annotations=[],
                    had_policy_intervention=True,
                    response_was_modified=True,
                ),
            ],
            total_policy_interventions=1,
            models_used=["claude-3-opus"],
        )

        result = export_session_jsonl(session)
        record = json.loads(result)

        assert record["response_was_modified"] is True
        assert record["request_was_modified"] is False
        assert record["original_response_messages"][0]["content"] == "Hi"
        assert "original_request_messages" not in record
