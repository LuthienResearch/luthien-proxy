"""Unit tests for S3ConversationArchiver.

Covers:
- archive_calls: serializes per-call records (call + events + policy_events +
  judge_decisions) to JSONL and uploads as one PUT
- Cursor pagination across multiple call batches
- Encryption modes (AES256, aws:kms with/without key id)
- Empty / S3-error / no-bucket-no-boto3 paths
- Returned call_ids match what was archived
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.retention.archiver import S3ConversationArchiver

_DEFAULT_SETTINGS = MagicMock(retention_s3_encryption="AES256", retention_s3_kms_key_id="")


def _patch_settings(settings: Any = _DEFAULT_SETTINGS):
    return patch("luthien_proxy.retention.archiver.get_settings", return_value=settings)


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
        "sequence": sequence,
        "payload": payload,
        "created_at": datetime(2024, 1, 1, 12, 0, sequence, tzinfo=UTC),
        "session_id": None,
    }


def _make_conn(*, call_batches: list[list[dict[str, Any]]], events_by_call: dict[str, list[dict[str, Any]]]) -> AsyncMock:
    """Build a fake DB conn whose `fetch` returns calls, then events, then empty for child tables."""
    conn = AsyncMock()
    fetch_responses: list[list[dict[str, Any]]] = []
    for batch in call_batches:
        fetch_responses.append(batch)  # call batch
        if batch:
            # events lookup for this batch
            events: list[dict[str, Any]] = []
            for call in batch:
                events.extend(events_by_call.get(call["call_id"], []))
            fetch_responses.append(events)
            fetch_responses.append([])  # policy_events
            fetch_responses.append([])  # judge_decisions
    conn.fetch = AsyncMock(side_effect=fetch_responses)
    return conn


@pytest.fixture
def mock_s3_client() -> MagicMock:
    client = MagicMock()
    client.put_object = MagicMock()
    return client


@pytest.fixture
def cutoff() -> datetime:
    return datetime(2024, 1, 3, tzinfo=UTC)


def test_archiver_init_defaults():
    archiver = S3ConversationArchiver(bucket="b")
    assert archiver.bucket == "b"
    assert archiver.prefix == "luthien-archive/"
    assert archiver.batch_size == 1000


def test_archiver_init_overrides():
    archiver = S3ConversationArchiver(bucket="b", prefix="p/", batch_size=42)
    assert archiver.prefix == "p/"
    assert archiver.batch_size == 42


def test_build_s3_key_format():
    archiver = S3ConversationArchiver(bucket="b", prefix="p/")
    key = archiver._build_s3_key(datetime(2024, 3, 15, 10, 30, tzinfo=UTC), run_id="abc12345", batch_index=2)
    assert key.startswith("p/2024-03-15/")
    assert "abc12345" in key
    assert key.endswith("-0002.jsonl")


@pytest.mark.asyncio
async def test_archive_calls_uploads_per_call_jsonl(mock_s3_client, cutoff):
    """Each output line is one full conversation record."""
    calls = [_make_call("call-001"), _make_call("call-002")]
    events = {
        "call-001": [_make_event("call-001", 1, {"prompt": "hi"}), _make_event("call-001", 2, {"reply": "hello"})],
        "call-002": [],
    }
    conn = _make_conn(call_batches=[calls], events_by_call=events)

    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)
        archived_ids = await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    assert archived_ids == ["call-001", "call-002"]
    mock_s3_client.put_object.assert_called_once()
    body = mock_s3_client.put_object.call_args.kwargs["Body"]
    lines = [json.loads(line) for line in body.decode().splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["call"]["call_id"] == "call-001"
    assert len(lines[0]["events"]) == 2
    assert lines[0]["policy_events"] == []
    assert lines[0]["judge_decisions"] == []
    assert lines[1]["call"]["call_id"] == "call-002"
    assert lines[1]["events"] == []


@pytest.mark.asyncio
async def test_archive_calls_no_op_when_empty(mock_s3_client, cutoff):
    conn = _make_conn(call_batches=[[]], events_by_call={})
    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        archived_ids = await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    assert archived_ids == []
    mock_s3_client.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_archive_calls_paginates_one_put_per_batch(mock_s3_client, cutoff):
    """Each batch becomes its own S3 object — bounded memory across the run."""
    batch_size = 2
    batch1 = [_make_call("call-001"), _make_call("call-002")]
    batch2 = [_make_call("call-003")]
    conn = _make_conn(
        call_batches=[batch1, batch2],
        events_by_call={"call-001": [], "call-002": [], "call-003": []},
    )

    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client, batch_size=batch_size)
        archived_ids = await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    assert archived_ids == ["call-001", "call-002", "call-003"]
    # Two PUTs, one per batch — not one combined upload.
    assert mock_s3_client.put_object.call_count == 2

    call_batch_fetches = [c for c in conn.fetch.call_args_list if "FROM conversation_calls" in c.args[0]]
    assert len(call_batch_fetches) == 2
    assert call_batch_fetches[1].args[2] == "call-002"

    # Both batches share a run_id portion in their key.
    keys = [call.kwargs["Key"] for call in mock_s3_client.put_object.call_args_list]
    # Key shape: prefix/YYYY-MM-DD/timestamp-runid-NNNN.jsonl
    suffixes = [k.rsplit("-", 1)[1] for k in keys]
    assert suffixes == ["0000.jsonl", "0001.jsonl"]
    runids = ["-".join(k.rsplit("-", 2)[1:2]) for k in keys]
    assert runids[0] == runids[1]


@pytest.mark.asyncio
async def test_archive_calls_first_batch_failure_returns_empty(mock_s3_client, cutoff):
    """When the first S3 PUT fails, return an empty list — purger must skip deletion."""
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    mock_s3_client.put_object = MagicMock(side_effect=RuntimeError("S3 down"))
    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        archived_ids = await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    assert archived_ids == []


@pytest.mark.asyncio
async def test_archive_calls_partial_success_returns_durably_uploaded_ids(mock_s3_client, cutoff):
    """First batch uploads; second batch fails. Return only the first batch's call_ids."""
    batch_size = 2
    batch1 = [_make_call("call-001"), _make_call("call-002")]
    batch2 = [_make_call("call-003"), _make_call("call-004")]
    conn = _make_conn(
        call_batches=[batch1, batch2],
        events_by_call={c["call_id"]: [] for c in batch1 + batch2},
    )
    # Succeed first call, fail second.
    mock_s3_client.put_object = MagicMock(side_effect=[None, RuntimeError("S3 down")])

    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client, batch_size=batch_size)
        archived_ids = await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    assert archived_ids == ["call-001", "call-002"]
    assert mock_s3_client.put_object.call_count == 2


@pytest.mark.asyncio
async def test_archive_calls_datetime_iso_format(mock_s3_client, cutoff):
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    body = mock_s3_client.put_object.call_args.kwargs["Body"]
    record = json.loads(body.decode().splitlines()[0])
    assert isinstance(record["call"]["created_at"], str)
    # ISO-8601 with offset
    assert "T" in record["call"]["created_at"]


@pytest.mark.asyncio
async def test_s3_upload_includes_aes256(mock_s3_client, cutoff):
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    kwargs = mock_s3_client.put_object.call_args.kwargs
    assert kwargs["ServerSideEncryption"] == "AES256"
    assert "SSEKMSKeyId" not in kwargs


@pytest.mark.asyncio
async def test_s3_upload_kms_with_key(mock_s3_client, cutoff):
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    settings = MagicMock(retention_s3_encryption="aws:kms", retention_s3_kms_key_id="arn:aws:kms:us-east-1:0:key/x")
    with _patch_settings(settings):
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    kwargs = mock_s3_client.put_object.call_args.kwargs
    assert kwargs["ServerSideEncryption"] == "aws:kms"
    assert kwargs["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:0:key/x"


@pytest.mark.asyncio
async def test_s3_upload_kms_without_key_raises(mock_s3_client, cutoff):
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    settings = MagicMock(retention_s3_encryption="aws:kms", retention_s3_kms_key_id="")
    with _patch_settings(settings):
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        with pytest.raises(ValueError, match="RETENTION_S3_KMS_KEY_ID"):
            await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    mock_s3_client.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_s3_upload_bucket_default_omits_sse_headers(mock_s3_client, cutoff):
    """`bucket-default` mode lets the bucket policy apply — no SSE header on the PUT."""
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    settings = MagicMock(retention_s3_encryption="bucket-default", retention_s3_kms_key_id="")
    with _patch_settings(settings):
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    kwargs = mock_s3_client.put_object.call_args.kwargs
    assert "ServerSideEncryption" not in kwargs
    assert "SSEKMSKeyId" not in kwargs


@pytest.mark.asyncio
async def test_s3_upload_invalid_encryption_raises(mock_s3_client, cutoff):
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": []},
    )
    settings = MagicMock(retention_s3_encryption="rot13", retention_s3_kms_key_id="")
    with _patch_settings(settings):
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        with pytest.raises(ValueError, match="rot13"):
            await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    mock_s3_client.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_jsonb_string_is_reparsed_into_structure(mock_s3_client, cutoff):
    """SQLite stores JSONB as a TEXT blob. The archiver re-parses it so the archive contains structure, not a quoted string."""
    payload_json_text = '{"role":"user","content":"hi"}'
    event = _make_event("call-001", 1, {})
    event["payload"] = payload_json_text  # simulate raw TEXT from sqlite
    conn = _make_conn(
        call_batches=[[_make_call("call-001")]],
        events_by_call={"call-001": [event]},
    )
    with _patch_settings():
        archiver = S3ConversationArchiver(bucket="b", s3_client=mock_s3_client)
        await archiver.archive_calls(db_conn=conn, cutoff=cutoff)
    body = mock_s3_client.put_object.call_args.kwargs["Body"]
    record = json.loads(body.decode().splitlines()[0])
    assert record["events"][0]["payload"] == {"role": "user", "content": "hi"}
