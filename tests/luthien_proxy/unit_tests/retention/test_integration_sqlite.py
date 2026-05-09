"""SQLite integration tests for the retention pipeline.

These tests run against a real in-memory SQLite database with the actual
migration set applied. They catch bugs that mocked-`fetchval` unit tests
miss: column mismatches, parameter-translation issues, cascade behavior,
and JSON column round-tripping. They are still classified as unit tests
because they hold no external dependencies (no Docker, no S3, no Postgres).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from luthien_proxy.retention.archiver import S3ConversationArchiver
from luthien_proxy.retention.purger import ConversationPurger
from luthien_proxy.utils import db as db_module
from luthien_proxy.utils.migration_check import check_migrations


@pytest.fixture
async def sqlite_pool():
    """A real in-memory SQLite DatabasePool with all migrations applied."""
    pool = db_module.DatabasePool("sqlite://:memory:")
    await check_migrations(pool)
    try:
        yield pool
    finally:
        await pool.close()


async def _insert_call(
    pool: db_module.DatabasePool,
    *,
    call_id: str,
    created_at: datetime,
    status: str = "completed",
    session_id: str | None = None,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversation_calls (call_id, model_name, provider, status, created_at, session_id)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            call_id,
            "claude-3",
            "anthropic",
            status,
            created_at,
            session_id,
        )


async def _insert_event(
    pool: db_module.DatabasePool,
    *,
    call_id: str,
    sequence: int,
    payload: dict[str, Any],
    event_type: str = "request",
) -> None:
    async with pool.connection() as conn:
        # Generate a uuid-shaped id manually since SQLite has no uuid_generate_v4.
        # `sequence` was dropped from conversation_events in migration 004; events
        # are ordered by created_at now. We still take a sequence param so tests
        # can encode the relative ordering they care about into the id.
        event_id = f"{call_id}-{sequence:04d}"
        await conn.execute(
            "INSERT INTO conversation_events (id, call_id, event_type, payload)"
            " VALUES ($1, $2, $3, $4)",
            event_id,
            call_id,
            event_type,
            payload,
        )


async def _insert_policy_event(
    pool: db_module.DatabasePool,
    *,
    call_id: str,
    policy_class: str,
    metadata: dict[str, Any],
) -> str:
    pe_id = f"pe-{call_id}-{policy_class}"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO policy_events (id, call_id, policy_class, event_type, metadata)"
            " VALUES ($1, $2, $3, $4, $5)",
            pe_id,
            call_id,
            policy_class,
            "decision",
            metadata,
        )
    return pe_id


async def _insert_judge_decision(
    pool: db_module.DatabasePool,
    *,
    call_id: str,
    judge_prompt: dict[str, Any],
    judge_response_text: str,
) -> str:
    jd_id = f"jd-{call_id}"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversation_judge_decisions"
            " (id, call_id, judge_prompt, judge_response_text)"
            " VALUES ($1, $2, $3, $4)",
            jd_id,
            call_id,
            judge_prompt,
            judge_response_text,
        )
    return jd_id


async def _count(pool: db_module.DatabasePool, table: str) -> int:
    async with pool.connection() as conn:
        result = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        return int(result) if result is not None else 0


@pytest.mark.asyncio
async def test_purge_with_archiver_against_real_sqlite(sqlite_pool):
    """End-to-end: real SQLite, real migrations, real INSERTs, mock S3.

    Verifies:
    - Archiver fetches calls + child events without column errors
    - Cascade deletes wipe child rows when their parent call is deleted
    - Recent rows survive
    - The S3 payload contains structured event payloads (JSON re-parsed)
    """
    now = datetime.now(UTC)
    old1 = now - timedelta(days=40)
    old2 = now - timedelta(days=35)
    fresh = now - timedelta(days=1)

    await _insert_call(sqlite_pool, call_id="old-1", created_at=old1, session_id="s1")
    await _insert_call(sqlite_pool, call_id="old-2", created_at=old2)
    await _insert_call(sqlite_pool, call_id="fresh-1", created_at=fresh)

    await _insert_event(sqlite_pool, call_id="old-1", sequence=1, payload={"role": "user", "content": "hi"})
    await _insert_event(sqlite_pool, call_id="old-1", sequence=2, payload={"role": "assistant", "content": "hello"})
    await _insert_event(sqlite_pool, call_id="old-2", sequence=1, payload={"role": "user", "content": "ping"})
    await _insert_event(sqlite_pool, call_id="fresh-1", sequence=1, payload={"role": "user", "content": "stay"})

    s3_client = MagicMock()
    s3_client.put_object = MagicMock()

    archiver = S3ConversationArchiver(bucket="b", s3_client=s3_client, batch_size=10)
    purger = ConversationPurger(db_pool=sqlite_pool, retention_days=30, archiver=archiver)

    deleted = await purger.purge_once()

    assert deleted == 2  # only the two old calls
    assert await _count(sqlite_pool, "conversation_calls") == 1
    assert await _count(sqlite_pool, "conversation_events") == 1  # cascade fired

    # Verify the surviving rows are the fresh ones
    async with sqlite_pool.connection() as conn:
        surviving_call = await conn.fetchval("SELECT call_id FROM conversation_calls")
        assert surviving_call == "fresh-1"
        surviving_event = await conn.fetchval("SELECT call_id FROM conversation_events")
        assert surviving_event == "fresh-1"

    # Verify the S3 payload
    s3_client.put_object.assert_called_once()
    # Key shape: {prefix}{run-date}/cutoff-{cutoff-date}-{ts}-{run_id}-{batch:04d}.jsonl
    key = s3_client.put_object.call_args.kwargs["Key"]
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert key.startswith(f"luthien-archive/{today}/cutoff-")
    assert key.endswith("-0000.jsonl")
    body = s3_client.put_object.call_args.kwargs["Body"].decode()
    lines = [json.loads(line) for line in body.splitlines() if line]
    assert len(lines) == 2
    archived_call_ids = {line["call"]["call_id"] for line in lines}
    assert archived_call_ids == {"old-1", "old-2"}

    by_id = {line["call"]["call_id"]: line for line in lines}
    assert len(by_id["old-1"]["events"]) == 2
    assert len(by_id["old-2"]["events"]) == 1
    # Crucial: payloads must be re-parsed into dicts, not double-encoded strings.
    assert by_id["old-1"]["events"][0]["payload"]["role"] in {"user", "assistant"}
    assert by_id["old-1"]["call"]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_purge_without_archiver_against_real_sqlite(sqlite_pool):
    """No-archive path: SQLite count-then-delete must work end-to-end."""
    now = datetime.now(UTC)
    await _insert_call(sqlite_pool, call_id="old-a", created_at=now - timedelta(days=40))
    await _insert_call(sqlite_pool, call_id="old-b", created_at=now - timedelta(days=31))
    await _insert_call(sqlite_pool, call_id="fresh", created_at=now - timedelta(days=2))

    purger = ConversationPurger(db_pool=sqlite_pool, retention_days=30)
    deleted = await purger.purge_once()

    assert deleted == 2
    assert await _count(sqlite_pool, "conversation_calls") == 1


@pytest.mark.asyncio
async def test_purge_archive_failure_leaves_data_intact(sqlite_pool):
    """If the first S3 upload fails, no rows are deleted."""
    await _insert_call(
        sqlite_pool,
        call_id="old-1",
        created_at=datetime.now(UTC) - timedelta(days=40),
    )

    s3_client = MagicMock()
    s3_client.put_object = MagicMock(side_effect=RuntimeError("S3 down"))
    archiver = S3ConversationArchiver(bucket="b", s3_client=s3_client)
    purger = ConversationPurger(db_pool=sqlite_pool, retention_days=30, archiver=archiver)

    deleted = await purger.purge_once()

    assert deleted == 0
    assert await _count(sqlite_pool, "conversation_calls") == 1


@pytest.mark.asyncio
async def test_purge_partial_run_archives_and_deletes_first_batch_only(sqlite_pool):
    """First batch uploads + deletes; second batch's upload fails. Surviving rows
    remain so the next run can retry. This is the failure mode that motivated
    the per-batch architecture."""
    now = datetime.now(UTC)
    for i in range(3):
        await _insert_call(
            sqlite_pool, call_id=f"old-{i}", created_at=now - timedelta(days=40 + i)
        )

    s3_client = MagicMock()
    s3_client.put_object = MagicMock(side_effect=[None, RuntimeError("S3 flapped")])
    archiver = S3ConversationArchiver(bucket="b", s3_client=s3_client, batch_size=2)
    purger = ConversationPurger(db_pool=sqlite_pool, retention_days=30, archiver=archiver)

    deleted = await purger.purge_once()

    # Batch 0 (2 calls) durably archived + deleted; batch 1 archive failed
    # before delete -> the third old call survives for next run.
    assert deleted == 2
    assert await _count(sqlite_pool, "conversation_calls") == 1
    assert s3_client.put_object.call_count == 2


@pytest.mark.asyncio
async def test_archive_includes_policy_events_and_judge_decisions(sqlite_pool):
    """The archive must contain the structured payloads from policy_events
    and conversation_judge_decisions — those tables hold what's actually
    valuable for "we keep records for technical reference". The previous
    metadata-only archive design lost this content on cascade delete."""
    now = datetime.now(UTC)
    old = now - timedelta(days=40)

    await _insert_call(sqlite_pool, call_id="old-1", created_at=old)
    await _insert_event(sqlite_pool, call_id="old-1", sequence=1, payload={"role": "user", "content": "hi"})
    await _insert_policy_event(
        sqlite_pool,
        call_id="old-1",
        policy_class="luthien_proxy.policies.NoOpPolicy",
        metadata={"verdict": "allow", "reason": "no rule matched"},
    )
    await _insert_judge_decision(
        sqlite_pool,
        call_id="old-1",
        judge_prompt={"system": "you are a judge", "user": "evaluate this"},
        judge_response_text="approved",
    )

    s3_client = MagicMock()
    s3_client.put_object = MagicMock()
    archiver = S3ConversationArchiver(bucket="b", s3_client=s3_client)
    purger = ConversationPurger(db_pool=sqlite_pool, retention_days=30, archiver=archiver)

    deleted = await purger.purge_once()

    assert deleted == 1
    # Cascade: child rows are gone from DB.
    assert await _count(sqlite_pool, "conversation_events") == 0
    assert await _count(sqlite_pool, "policy_events") == 0
    assert await _count(sqlite_pool, "conversation_judge_decisions") == 0

    # Archive content: every child record's payload survives, structured.
    body = s3_client.put_object.call_args.kwargs["Body"].decode()
    record = json.loads(body.splitlines()[0])
    assert record["call"]["call_id"] == "old-1"
    assert len(record["events"]) == 1
    assert len(record["policy_events"]) == 1
    assert record["policy_events"][0]["metadata"] == {"verdict": "allow", "reason": "no rule matched"}
    assert len(record["judge_decisions"]) == 1
    assert record["judge_decisions"][0]["judge_prompt"] == {
        "system": "you are a judge",
        "user": "evaluate this",
    }
    assert record["judge_decisions"][0]["judge_response_text"] == "approved"


@pytest.mark.asyncio
async def test_purge_with_archiver_no_old_rows_uploads_nothing(sqlite_pool):
    """Empty archive window must not upload an empty file or DELETE anything."""
    await _insert_call(
        sqlite_pool,
        call_id="fresh",
        created_at=datetime.now(UTC) - timedelta(days=1),
    )

    s3_client = MagicMock()
    s3_client.put_object = MagicMock()
    archiver = S3ConversationArchiver(bucket="b", s3_client=s3_client)
    purger = ConversationPurger(db_pool=sqlite_pool, retention_days=30, archiver=archiver)

    deleted = await purger.purge_once()

    assert deleted == 0
    assert await _count(sqlite_pool, "conversation_calls") == 1
    s3_client.put_object.assert_not_called()
