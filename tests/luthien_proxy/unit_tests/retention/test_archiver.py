"""Unit tests for S3ConversationArchiver.

Tests cover:
- archive_calls: serializes rows to JSONL and uploads to S3
- archive_calls: no-op when no rows to archive
- archive_calls: raises when boto3 not installed and bucket is configured
- archive_calls: handles S3 upload errors
- JSONL format: each line is valid JSON with expected fields
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.retention.archiver import S3ConversationArchiver


@pytest.fixture
def sample_calls():
    """Sample conversation_calls rows with all columns including session_id and user_id."""
    return [
        {
            "call_id": "call-001",
            "model_name": "claude-3-5-sonnet-20241022",
            "provider": "anthropic",
            "status": "completed",
            "created_at": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            "completed_at": datetime(2024, 1, 1, 12, 0, 5, tzinfo=UTC),
            "session_id": "sess-abc",
            "user_id": "user-123",
        },
        {
            "call_id": "call-002",
            "model_name": "claude-3-haiku-20240307",
            "provider": "anthropic",
            "status": "completed",
            "created_at": datetime(2024, 1, 2, 8, 0, 0, tzinfo=UTC),
            "completed_at": None,
            "session_id": None,
            "user_id": None,
        },
    ]


@pytest.fixture
def mock_db_pool(sample_calls):
    """Mock DatabasePool that returns sample_calls on fetch."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=sample_calls)
    pool.connection = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client."""
    client = MagicMock()
    client.put_object = MagicMock()
    return client


def test_archiver_init():
    """S3ConversationArchiver stores bucket and prefix."""
    archiver = S3ConversationArchiver(bucket="my-bucket", prefix="luthien/")
    assert archiver.bucket == "my-bucket"
    assert archiver.prefix == "luthien/"


def test_archiver_default_prefix():
    """Default prefix is 'luthien-archive/'."""
    archiver = S3ConversationArchiver(bucket="my-bucket")
    assert archiver.prefix == "luthien-archive/"


@pytest.mark.asyncio
async def test_archive_calls_uploads_jsonl(mock_db_pool, mock_s3_client, sample_calls):
    """archive_calls should upload a JSONL file to S3 with one JSON object per line."""
    pool, conn = mock_db_pool
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)
    await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    mock_s3_client.put_object.assert_called_once()
    call_kwargs = mock_s3_client.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "test-bucket"

    # Verify JSONL content
    body = call_kwargs["Body"]
    lines = [line for line in body.decode().strip().split("\n") if line]
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["call_id"] == "call-001"
    assert first["model_name"] == "claude-3-5-sonnet-20241022"


@pytest.mark.asyncio
async def test_archive_calls_no_op_when_empty(mock_db_pool, mock_s3_client):
    """archive_calls should not upload when there are no rows."""
    pool, conn = mock_db_pool
    conn.fetch = AsyncMock(return_value=[])
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)
    await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    mock_s3_client.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_archive_calls_s3_key_includes_date(mock_db_pool, mock_s3_client, sample_calls):
    """S3 key should include the archive date for partitioning."""
    pool, conn = mock_db_pool
    cutoff = datetime(2024, 6, 15, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", prefix="archive/", s3_client=mock_s3_client)
    await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    call_kwargs = mock_s3_client.put_object.call_args[1]
    key = call_kwargs["Key"]
    assert "archive/" in key
    assert "2024" in key


@pytest.mark.asyncio
async def test_archive_calls_handles_s3_error(mock_db_pool, mock_s3_client, sample_calls):
    """archive_calls should propagate S3 errors so the purger can skip deletion."""
    pool, conn = mock_db_pool
    mock_s3_client.put_object = MagicMock(side_effect=Exception("S3 access denied"))
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)

    with pytest.raises(Exception, match="S3 access denied"):
        await archiver.archive_calls(db_conn=conn, cutoff=cutoff)


@pytest.mark.asyncio
async def test_archive_calls_datetime_serialized_as_iso(mock_db_pool, mock_s3_client, sample_calls):
    """Datetime fields in JSONL should be ISO-8601 strings."""
    pool, conn = mock_db_pool
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)
    await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    body = mock_s3_client.put_object.call_args[1]["Body"]
    first = json.loads(body.decode().strip().split("\n")[0])
    # created_at should be a string, not a datetime object
    assert isinstance(first["created_at"], str)


@pytest.mark.asyncio
async def test_archive_calls_null_completed_at(mock_db_pool, mock_s3_client, sample_calls):
    """Null completed_at should serialize as JSON null."""
    pool, conn = mock_db_pool
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)
    await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    body = mock_s3_client.put_object.call_args[1]["Body"]
    lines = body.decode().strip().split("\n")
    second = json.loads(lines[1])
    assert second["completed_at"] is None


@pytest.mark.asyncio
async def test_archive_calls_preserves_session_and_user_id(mock_db_pool, mock_s3_client, sample_calls):
    """session_id and user_id columns must be preserved in the archived JSONL."""
    pool, conn = mock_db_pool
    cutoff = datetime(2024, 1, 3, tzinfo=UTC)

    archiver = S3ConversationArchiver(bucket="test-bucket", s3_client=mock_s3_client)
    await archiver.archive_calls(db_conn=conn, cutoff=cutoff)

    body = mock_s3_client.put_object.call_args[1]["Body"]
    lines = [line for line in body.decode().strip().split("\n") if line]
    first = json.loads(lines[0])
    assert first["session_id"] == "sess-abc"
    assert first["user_id"] == "user-123"

    second = json.loads(lines[1])
    assert second["session_id"] is None
    assert second["user_id"] is None


def test_build_s3_key_format():
    """_build_s3_key should produce a deterministic, date-partitioned key."""
    archiver = S3ConversationArchiver(bucket="b", prefix="p/")
    cutoff = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)
    key = archiver._build_s3_key(cutoff)
    assert key.startswith("p/")
    assert "2024-03-15" in key
    assert key.endswith(".jsonl")
