"""Unit tests for S3ConversationArchiver.

The archiver now exposes only `archive_one_batch`; the per-batch loop and
the per-batch DELETE belong to the purger. These tests cover:

- Constructor-time validation of encryption settings
- Per-batch JSONL serialization (call + events + policy_events + judge_decisions)
- Encryption modes: AES256, aws:kms (with/without key id), bucket-default
- S3 error propagation (the purger handles "stop the run" semantics)
- Boto3-not-installed error
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.retention.archiver import VALID_ENCRYPTION_MODES, S3ConversationArchiver


def _make_call(call_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "call_id": call_id,
        "model_name": "claude-3-5-sonnet-20241022",
        "provider": "anthropic",
        "status": "completed",
        "created_at": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        "completed_at": datetime(2024, 1, 1, 12, 0, 5, tzinfo=UTC),
        "session_id": None,
    }
    base.update(overrides)
    return base


def _make_event(call_id: str, sequence: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"evt-{call_id}-{sequence}",
        "call_id": call_id,
        "event_type": "request",
        "payload": payload,
        "created_at": datetime(2024, 1, 1, 12, 0, sequence, tzinfo=UTC),
        "session_id": None,
    }


def _make_conn_for_one_batch(
    *, calls: list[dict[str, Any]], events_by_call: dict[str, list[dict[str, Any]]]
) -> AsyncMock:
    """Build a fake DB conn for one archive_one_batch call.

    archive_one_batch fetches: calls, then events, then policy_events, then judge_decisions.
    """
    conn = AsyncMock()
    events: list[dict[str, Any]] = []
    for call in calls:
        events.extend(events_by_call.get(call["call_id"], []))
    conn.fetch = AsyncMock(side_effect=[calls, events, [], []])
    return conn


@pytest.fixture
def mock_s3_client() -> MagicMock:
    client = MagicMock()
    client.put_object = MagicMock()
    return client


@pytest.fixture
def cutoff() -> datetime:
    return datetime(2024, 1, 3, tzinfo=UTC)


# ── construction-time validation ──────────────────────────────────────────


def test_archiver_init_defaults():
    archiver = S3ConversationArchiver(bucket="b")
    assert archiver.bucket == "b"
    assert archiver.prefix == "luthien-archive/"
    assert archiver.batch_size == 100


def test_archiver_init_overrides():
    archiver = S3ConversationArchiver(bucket="b", prefix="p/", batch_size=42)
    assert archiver.prefix == "p/"
    assert archiver.batch_size == 42


def test_archiver_init_rejects_invalid_encryption_mode():
    with pytest.raises(ValueError, match="rot13"):
        S3ConversationArchiver(bucket="b", encryption_mode="rot13")


def test_archiver_init_rejects_kms_without_key_id():
    with pytest.raises(ValueError, match="kms_key_id"):
        S3ConversationArchiver(bucket="b", encryption_mode="aws:kms", kms_key_id="")


def test_archiver_init_accepts_kms_with_key_id():
    arch = S3ConversationArchiver(
        bucket="b", encryption_mode="aws:kms", kms_key_id="arn:aws:kms:us-east-1:0:key/x"
    )
    assert arch._encryption_mode == "aws:kms"


def test_archiver_init_accepts_bucket_default():
    arch = S3ConversationArchiver(bucket="b", encryption_mode="bucket-default")
    assert arch._encryption_mode == "bucket-default"


def test_valid_encryption_modes_constant():
    assert VALID_ENCRYPTION_MODES == frozenset({"AES256", "aws:kms", "bucket-default"})


# ── archive_one_batch happy paths ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_one_batch_uploads_jsonl_with_full_records(mock_s3_client, cutoff):
    calls = [_make_call("call-001"), _make_call("call-002")]
    events = {
        "call-001": [_make_event("call-001", 1, {"prompt": "hi"})],
        "call-002": [],
    }
    conn = _make_conn_for_one_batch(calls=calls, events_by_call=events)

    archiver = S3ConversationArchiver(bucket="b", batch_size=10, s3_client=mock_s3_client)
    archived_ids, has_more = await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="run0001", batch_index=0
    )

    assert archived_ids == ["call-001", "call-002"]
    assert has_more is False  # 2 < batch_size 10
    mock_s3_client.put_object.assert_called_once()
    body = mock_s3_client.put_object.call_args.kwargs["Body"]
    lines = [json.loads(line) for line in body.decode().splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["call"]["call_id"] == "call-001"
    assert lines[0]["events"][0]["payload"] == {"prompt": "hi"}
    assert lines[0]["policy_events"] == []
    assert lines[0]["judge_decisions"] == []


@pytest.mark.asyncio
async def test_archive_one_batch_full_signals_has_more(mock_s3_client, cutoff):
    """When the batch is full, has_more=True so the purger keeps looping."""
    calls = [_make_call(f"call-{i:03d}") for i in range(3)]
    conn = _make_conn_for_one_batch(calls=calls, events_by_call={c["call_id"]: [] for c in calls})

    archiver = S3ConversationArchiver(bucket="b", batch_size=3, s3_client=mock_s3_client)
    archived_ids, has_more = await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="run0001", batch_index=0
    )

    assert archived_ids == ["call-000", "call-001", "call-002"]
    assert has_more is True


@pytest.mark.asyncio
async def test_archive_one_batch_no_rows_short_circuits(mock_s3_client, cutoff):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
    archived_ids, has_more = await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="run0001", batch_index=0
    )

    assert archived_ids == []
    assert has_more is False
    mock_s3_client.put_object.assert_not_called()
    # Only the call fetch happened; no child fetches.
    assert conn.fetch.call_count == 1


@pytest.mark.asyncio
async def test_archive_one_batch_uses_cursor_when_last_call_id_set(mock_s3_client, cutoff):
    calls = [_make_call("call-005")]
    conn = _make_conn_for_one_batch(calls=calls, events_by_call={"call-005": []})

    archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
    await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id="call-004", run_id="r", batch_index=1
    )

    first_fetch_args = conn.fetch.call_args_list[0].args
    # SQL contains the cursor predicate
    assert "call_id > $2" in first_fetch_args[0]
    # last_call_id is the second positional param
    assert first_fetch_args[2] == "call-004"


@pytest.mark.asyncio
async def test_archive_one_batch_propagates_s3_error(mock_s3_client, cutoff):
    conn = _make_conn_for_one_batch(calls=[_make_call("call-001")], events_by_call={"call-001": []})
    mock_s3_client.put_object = MagicMock(side_effect=RuntimeError("S3 down"))

    archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
    with pytest.raises(RuntimeError, match="S3 down"):
        await archiver.archive_one_batch(
            db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="r", batch_index=0
        )


# ── encryption modes on the put_object kwargs ─────────────────────────────


@pytest.mark.asyncio
async def test_aes256_mode_includes_sse_header(mock_s3_client, cutoff):
    conn = _make_conn_for_one_batch(calls=[_make_call("c1")], events_by_call={"c1": []})
    archiver = S3ConversationArchiver(bucket="b", encryption_mode="AES256", s3_client=mock_s3_client)
    await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="r", batch_index=0
    )
    kwargs = mock_s3_client.put_object.call_args.kwargs
    assert kwargs["ServerSideEncryption"] == "AES256"
    assert "SSEKMSKeyId" not in kwargs


@pytest.mark.asyncio
async def test_kms_mode_includes_kms_key_id(mock_s3_client, cutoff):
    conn = _make_conn_for_one_batch(calls=[_make_call("c1")], events_by_call={"c1": []})
    archiver = S3ConversationArchiver(
        bucket="b",
        encryption_mode="aws:kms",
        kms_key_id="arn:aws:kms:us-east-1:0:key/x",
        s3_client=mock_s3_client,
    )
    await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="r", batch_index=0
    )
    kwargs = mock_s3_client.put_object.call_args.kwargs
    assert kwargs["ServerSideEncryption"] == "aws:kms"
    assert kwargs["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:0:key/x"


@pytest.mark.asyncio
async def test_bucket_default_omits_sse_headers(mock_s3_client, cutoff):
    conn = _make_conn_for_one_batch(calls=[_make_call("c1")], events_by_call={"c1": []})
    archiver = S3ConversationArchiver(bucket="b", encryption_mode="bucket-default", s3_client=mock_s3_client)
    await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="r", batch_index=0
    )
    kwargs = mock_s3_client.put_object.call_args.kwargs
    assert "ServerSideEncryption" not in kwargs
    assert "SSEKMSKeyId" not in kwargs


# ── boto3 lazy import ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_s3_client_raises_when_boto3_missing(cutoff):
    """Without an injected client, a missing boto3 import surfaces clearly."""
    archiver = S3ConversationArchiver(bucket="b")
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(RuntimeError, match="boto3 is not installed"):
            archiver._get_s3_client()


# ── object key shape ──────────────────────────────────────────────────────


def test_build_s3_key_partitions_by_run_date_not_cutoff():
    """The prefix date is run-date (today), not cutoff-date. Cutoff is
    encoded in the filename. Operators expect today's archives under today's
    prefix."""
    archiver = S3ConversationArchiver(bucket="b", prefix="p/")
    cutoff = datetime(2024, 3, 15, 10, 30, tzinfo=UTC)
    key = archiver._build_s3_key(cutoff, run_id="abc12345", batch_index=2)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert key.startswith(f"p/{today}/")
    assert "cutoff-2024-03-15" in key
    assert "abc12345" in key
    assert key.endswith("-0002.jsonl")


def test_prefix_without_trailing_slash_normalized():
    """A non-empty prefix without `/` would silently produce keys like
    'fooDATE/...' — the constructor normalizes it."""
    archiver = S3ConversationArchiver(bucket="b", prefix="luthien-archive")
    assert archiver.prefix == "luthien-archive/"


def test_empty_prefix_preserved():
    """Empty prefix means root-of-bucket — leave it alone."""
    archiver = S3ConversationArchiver(bucket="b", prefix="")
    assert archiver.prefix == ""


def test_prefix_with_trailing_slash_unchanged():
    archiver = S3ConversationArchiver(bucket="b", prefix="foo/bar/")
    assert archiver.prefix == "foo/bar/"


def test_new_run_id_is_short_hex():
    rid = S3ConversationArchiver.new_run_id()
    assert len(rid) == 8
    int(rid, 16)  # parses as hex


# ── jsonb re-parse helper ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jsonb_string_reparsed_into_structure(mock_s3_client, cutoff):
    """SQLite stores JSONB as TEXT; archiver re-parses so payloads stay structured."""
    payload_json_text = '{"role":"user","content":"hi"}'
    event = _make_event("c1", 1, {})
    event["payload"] = payload_json_text
    conn = _make_conn_for_one_batch(calls=[_make_call("c1")], events_by_call={"c1": [event]})

    archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
    await archiver.archive_one_batch(
        db_conn=conn, cutoff=cutoff, last_call_id=None, run_id="r", batch_index=0
    )
    body = mock_s3_client.put_object.call_args.kwargs["Body"]
    record = json.loads(body.decode().splitlines()[0])
    assert record["events"][0]["payload"] == {"role": "user", "content": "hi"}
