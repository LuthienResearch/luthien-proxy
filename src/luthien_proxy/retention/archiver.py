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

_VALID_ENCRYPTION_MODES: frozenset[str] = frozenset({"AES256", "aws:kms", "bucket-default"})

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

    def _build_s3_key(self, cutoff: datetime, run_id: str, batch_index: int) -> str:
        """Build a date-partitioned S3 key for one batch of an archive run.

        Format: {prefix}{YYYY-MM-DD}/{timestamp}-{run_id}-{batch:04d}.jsonl

        run_id is shared across batches in one purge; batch_index increments
        per batch. Together they make object listing / restore deterministic.
        """
        date_str = cutoff.strftime("%Y-%m-%d")
        ts_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{self.prefix}{date_str}/{ts_str}-{run_id}-{batch_index:04d}.jsonl"

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

    def _build_put_kwargs(self, key: str, body: bytes) -> dict[str, Any]:
        """Build S3 put_object kwargs, validating encryption settings up-front.

        Returns a dict ready to splat into ``s3.put_object``. Raises if the
        configured encryption mode is unknown or aws:kms without a key id.
        """
        settings = get_settings()
        mode = settings.retention_s3_encryption
        if mode not in _VALID_ENCRYPTION_MODES:
            raise ValueError(
                f"RETENTION_S3_ENCRYPTION={mode!r} is not valid. "
                f"Must be one of: {sorted(_VALID_ENCRYPTION_MODES)}"
            )
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/x-ndjson",
        }
        if mode == "bucket-default":
            return kwargs
        kwargs["ServerSideEncryption"] = mode
        if mode == "aws:kms":
            if not settings.retention_s3_kms_key_id:
                raise ValueError(
                    "RETENTION_S3_ENCRYPTION=aws:kms requires RETENTION_S3_KMS_KEY_ID to be set. "
                    "Leaving it unset silently falls back to the AWS-managed default key, "
                    "which is weaker than an explicitly configured customer-managed KMS key."
                )
            kwargs["SSEKMSKeyId"] = settings.retention_s3_kms_key_id
        return kwargs

    async def _build_batch_records(
        self, db_conn: Any, call_rows: list[Any]
    ) -> list[str]:
        """Build per-call JSONL lines for one batch of call rows."""
        call_ids = [row["call_id"] for row in call_rows]
        events = await self._fetch_children(db_conn, "conversation_events", _EVENT_COLUMNS, call_ids)
        policy_events = await self._fetch_children(
            db_conn, "policy_events", _POLICY_EVENT_COLUMNS, call_ids
        )
        judge_decisions = await self._fetch_children(
            db_conn, "conversation_judge_decisions", _JUDGE_COLUMNS, call_ids
        )
        lines: list[str] = []
        for row in call_rows:
            cid = row["call_id"]
            record = {
                "call": _row_to_dict(row, _CALL_COLUMNS),
                "events": events.get(cid, []),
                "policy_events": policy_events.get(cid, []),
                "judge_decisions": judge_decisions.get(cid, []),
            }
            lines.append(json.dumps(record))
        return lines

    async def archive_calls(self, *, db_conn: Any, cutoff: datetime) -> list[str]:
        """Archive full conversation records older than cutoff.

        Each output JSONL line is a self-contained record:

            {"call": {...}, "events": [...], "policy_events": [...], "judge_decisions": [...]}

        Each batch (`batch_size` calls + their descendants) is uploaded as its
        own S3 object, so peak memory is bounded to one batch — not the entire
        archive — and a partially-failing run still gets the earlier batches
        durably stored. Object keys share a per-run uuid so all batches from
        one purge cycle list together under the date prefix.

        Args:
            db_conn: An active DB connection (ConnectionProtocol). The caller
                is responsible for *not* wrapping this in a transaction — the
                archive can take seconds-to-minutes against a slow S3 endpoint.
            cutoff: Calls with `created_at < cutoff` will be archived.

        Returns:
            The list of call_ids that were successfully written to S3. May be
            shorter than the number of calls eligible for purge if a later
            batch failed; the caller should delete only the returned ids so
            unarchived rows survive for the next run to retry.

        Raises:
            ValueError: If encryption settings are invalid (raised before any
                upload attempt). Other S3 errors are caught per-batch; if no
                batch succeeds the function returns an empty list rather than
                raising, so the purger can defer deletion until next run.
        """
        # Validate encryption config before doing any work — no point fetching
        # batches if the upload will reject every one of them.
        self._build_put_kwargs(key="probe", body=b"")

        s3 = self._get_s3_client()
        run_id = uuid.uuid4().hex[:8]
        archived_ids: list[str] = []
        last_call_id: str | None = None
        batch_index = 0
        total_archived = 0

        while True:
            call_rows = await self._fetch_call_batch(db_conn, cutoff, last_call_id)
            if not call_rows:
                break

            lines = await self._build_batch_records(db_conn, call_rows)
            body = "\n".join(lines).encode("utf-8")
            key = self._build_s3_key(cutoff, run_id, batch_index)
            put_kwargs = self._build_put_kwargs(key=key, body=body)

            try:
                await asyncio.to_thread(s3.put_object, **put_kwargs)
            except Exception:
                logger.exception(
                    "S3 upload failed on batch %d (key=%s) — stopping archive run; "
                    "%d earlier batches persisted",
                    batch_index,
                    key,
                    batch_index,
                )
                # Stop the run. archived_ids holds only the call_ids whose
                # batches were durably uploaded. Subsequent runs will pick up
                # from where this one stopped (the cutoff predicate will still
                # match the unarchived rows) and re-emit them under a new
                # run_id, so no row is silently lost.
                break

            archived_ids.extend(row["call_id"] for row in call_rows)
            total_archived += len(call_rows)
            logger.info(
                "Archived batch %d (%d records) to s3://%s/%s",
                batch_index,
                len(call_rows),
                self.bucket,
                key,
            )
            batch_index += 1
            last_call_id = call_rows[-1]["call_id"]
            if len(call_rows) < self.batch_size:
                break

        if total_archived == 0:
            logger.debug("No conversation_calls to archive before %s", cutoff.isoformat())
        else:
            logger.info(
                "Archive run %s complete: %d records across %d batch(es)",
                run_id,
                total_archived,
                batch_index,
            )
        return archived_ids
