"""S3 archival for conversation data before purge.

For each `conversation_calls` row older than a cutoff, fetches the row and all
descendant rows (`conversation_events`, `policy_events`,
`conversation_judge_decisions`) and emits one JSONL line per call:

    {"call": {...}, "events": [...], "policy_events": [...], "judge_decisions": [...]}

This makes the archive useful as a technical reference: the actual
request/response payloads, policy decisions, and judge verdicts live in the
child tables, not on `conversation_calls` itself.

Archival runs *outside* the purger's DB transaction. The purger uploads first,
then opens a short transaction to delete only the call_ids that were
successfully archived. If S3 is slow or unavailable, the DB is not held
hostage; if the upload succeeds and the delete fails, a subsequent run will
re-archive the same rows (duplicate data in S3, no data loss).

boto3 is an optional dependency — imported lazily. If ARCHIVE_S3_BUCKET is
unset, this module is never instantiated and boto3 is never imported.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from luthien_proxy.settings import get_settings

logger = logging.getLogger(__name__)

_VALID_ENCRYPTION_MODES: frozenset[str] = frozenset({"AES256", "aws:kms"})

# Explicit column lists keep the archive shape stable across schema changes.
# `user_id` is intentionally absent — `conversation_calls` has no such column
# on the supported schema (only `session_id` was added in migration 006).
_CALL_COLUMNS = (
    "call_id",
    "model_name",
    "provider",
    "status",
    "created_at",
    "completed_at",
    "session_id",
)
_EVENT_COLUMNS = (
    "id",
    "call_id",
    "event_type",
    "payload",
    "created_at",
    "session_id",
)
_POLICY_EVENT_COLUMNS = (
    "id",
    "call_id",
    "policy_class",
    "policy_config",
    "event_type",
    "original_event_id",
    "modified_event_id",
    "metadata",
    "created_at",
)
_JUDGE_COLUMNS = (
    "id",
    "call_id",
    "trace_id",
    "tool_call_id",
    "probability",
    "explanation",
    "tool_call",
    "judge_prompt",
    "judge_response_text",
    "original_request",
    "original_response",
    "stream_chunks",
    "blocked_response",
    "timing",
    "judge_config",
    "created_at",
)


def _serialize_value(v: Any) -> Any:
    """Convert non-JSON-serializable values to JSON-safe equivalents."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, str):
        # JSONB columns arrive as strings on SQLite (and sometimes asyncpg).
        # Re-parse so the archive contains structured JSON, not a quoted blob.
        stripped = v.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return v
    return v


def _row_to_dict(row: Any, columns: Iterable[str]) -> dict[str, Any]:
    """Convert a DB row to a plain dict with the requested columns."""
    return {col: _serialize_value(row[col]) for col in columns}


def _select_clause(columns: Iterable[str]) -> str:
    return ", ".join(columns)


class S3ConversationArchiver:
    """Archives full conversation records to S3 as JSONL before purge.

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix (default: "luthien-archive/").
        batch_size: Calls fetched per cursor-based batch (default: 1000).
        s3_client: Optional pre-built boto3 S3 client (for testing). If None,
            a client is created lazily using boto3.client("s3").
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "luthien-archive/",
        batch_size: int = 1000,
        s3_client: Any = None,
    ) -> None:
        """Initialize archiver with S3 bucket, key prefix, batch size, and optional pre-built client."""
        self.bucket = bucket
        self.prefix = prefix
        self.batch_size = batch_size
        self._s3_client = s3_client

    def _get_s3_client(self) -> Any:
        """Return the S3 client, creating it lazily if needed."""
        if self._s3_client is not None:
            return self._s3_client
        try:
            import boto3  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "ARCHIVE_S3_BUCKET is set but boto3 is not installed. Install it with: pip install boto3"
            ) from exc
        self._s3_client = boto3.client("s3")
        return self._s3_client

    def _build_s3_key(self, cutoff: datetime) -> str:
        """Build a date-partitioned S3 key for the archive file.

        Format: {prefix}{YYYY-MM-DD}/{timestamp}-{uuid4}.jsonl
        """
        date_str = cutoff.strftime("%Y-%m-%d")
        ts_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{self.prefix}{date_str}/{ts_str}-{uuid.uuid4().hex[:8]}.jsonl"

    async def _fetch_call_batch(
        self,
        db_conn: Any,
        cutoff: datetime,
        last_call_id: str | None,
    ) -> list[Any]:
        """Fetch one batch of calls older than cutoff, paginated by call_id."""
        cols = _select_clause(_CALL_COLUMNS)
        if last_call_id is None:
            return await db_conn.fetch(
                f"SELECT {cols} FROM conversation_calls"
                " WHERE created_at < $1 ORDER BY call_id LIMIT $2",
                cutoff,
                self.batch_size,
            )
        return await db_conn.fetch(
            f"SELECT {cols} FROM conversation_calls"
            " WHERE created_at < $1 AND call_id > $2 ORDER BY call_id LIMIT $3",
            cutoff,
            last_call_id,
            self.batch_size,
        )

    async def _fetch_children(
        self,
        db_conn: Any,
        table: str,
        columns: tuple[str, ...],
        call_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Return a {call_id: [row_dict, ...]} map for a child table.

        Builds an `IN (?, ?, …)` clause by hand because both asyncpg and the
        SQLite shim accept positional `$N` placeholders but neither has a
        portable array-binding syntax.
        """
        if not call_ids:
            return {}
        placeholders = ",".join(f"${i + 1}" for i in range(len(call_ids)))
        cols = _select_clause(columns)
        rows = await db_conn.fetch(
            f"SELECT {cols} FROM {table} WHERE call_id IN ({placeholders})",
            *call_ids,
        )
        grouped: dict[str, list[dict[str, Any]]] = {cid: [] for cid in call_ids}
        for row in rows:
            grouped[row["call_id"]].append(_row_to_dict(row, columns))
        return grouped

    async def archive_calls(self, *, db_conn: Any, cutoff: datetime) -> list[str]:
        """Archive full conversation records older than cutoff.

        Each output JSONL line is a self-contained record:

            {"call": {...}, "events": [...], "policy_events": [...], "judge_decisions": [...]}

        Args:
            db_conn: An active DB connection (ConnectionProtocol). The caller
                is responsible for *not* wrapping this in a transaction — the
                archive can take seconds-to-minutes against a slow S3 endpoint.
            cutoff: Calls with `created_at < cutoff` will be archived.

        Returns:
            The list of call_ids that were successfully archived. The caller
            should delete only these (not the original cutoff filter), so
            late-arriving rows that match the cutoff between archive and
            delete are not silently dropped.

        Raises:
            Exception: If the S3 upload fails. The caller should catch this
                and skip deletion to avoid data loss.
        """
        jsonl_lines: list[str] = []
        archived_ids: list[str] = []
        last_call_id: str | None = None

        while True:
            call_rows = await self._fetch_call_batch(db_conn, cutoff, last_call_id)
            if not call_rows:
                break

            call_ids = [row["call_id"] for row in call_rows]
            events = await self._fetch_children(db_conn, "conversation_events", _EVENT_COLUMNS, call_ids)
            policy_events = await self._fetch_children(
                db_conn, "policy_events", _POLICY_EVENT_COLUMNS, call_ids
            )
            judge_decisions = await self._fetch_children(
                db_conn, "conversation_judge_decisions", _JUDGE_COLUMNS, call_ids
            )

            for row in call_rows:
                cid = row["call_id"]
                record = {
                    "call": _row_to_dict(row, _CALL_COLUMNS),
                    "events": events.get(cid, []),
                    "policy_events": policy_events.get(cid, []),
                    "judge_decisions": judge_decisions.get(cid, []),
                }
                jsonl_lines.append(json.dumps(record))
                archived_ids.append(cid)

            last_call_id = call_rows[-1]["call_id"]
            if len(call_rows) < self.batch_size:
                break

        if not jsonl_lines:
            logger.debug("No conversation_calls to archive before %s", cutoff.isoformat())
            return []

        total = len(jsonl_lines)
        logger.info("Archiving %d conversation records to s3://%s", total, self.bucket)

        body = "\n".join(jsonl_lines).encode("utf-8")
        key = self._build_s3_key(cutoff)

        s3 = self._get_s3_client()
        settings = get_settings()
        if settings.retention_s3_encryption not in _VALID_ENCRYPTION_MODES:
            raise ValueError(
                f"RETENTION_S3_ENCRYPTION={settings.retention_s3_encryption!r} is not valid. "
                f"Must be one of: {sorted(_VALID_ENCRYPTION_MODES)}"
            )
        put_kwargs = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/x-ndjson",
            "ServerSideEncryption": settings.retention_s3_encryption,
        }
        if settings.retention_s3_encryption == "aws:kms":
            if not settings.retention_s3_kms_key_id:
                raise ValueError(
                    "RETENTION_S3_ENCRYPTION=aws:kms requires RETENTION_S3_KMS_KEY_ID to be set. "
                    "Leaving it unset silently falls back to the AWS-managed default key, "
                    "which is weaker than an explicitly configured customer-managed KMS key."
                )
            put_kwargs["SSEKMSKeyId"] = settings.retention_s3_kms_key_id

        await asyncio.to_thread(s3.put_object, **put_kwargs)
        logger.info("Archived %d records to s3://%s/%s", total, self.bucket, key)
        return archived_ids
