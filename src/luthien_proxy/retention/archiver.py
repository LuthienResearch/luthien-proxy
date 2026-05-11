"""S3 archival for conversation data before purge.

For each `conversation_calls` row older than a cutoff, fetches the row and all
descendant rows (`conversation_events`, `policy_events`,
`conversation_judge_decisions`) and emits one JSONL line per call:

    {"call": {...}, "events": [...], "policy_events": [...], "judge_decisions": [...]}

This makes the archive useful as a technical reference: the actual
request/response payloads, policy decisions, and judge verdicts live in the
child tables, not on `conversation_calls` itself.

The archiver only handles a single batch's worth of work per call. The
purger drives the per-batch archive-then-delete loop so memory and DB
work are bounded to one batch — even on a first-run backfill of millions
of rows.

boto3 is an optional dependency — imported lazily. If `ARCHIVE_S3_BUCKET` is
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

logger = logging.getLogger(__name__)

VALID_ENCRYPTION_MODES: frozenset[str] = frozenset({"AES256", "aws:kms", "bucket-default"})

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
# `sequence` was dropped from conversation_events in migration 004; events
# are ordered by created_at now.
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
    """Convert non-JSON-serializable values to JSON-safe equivalents.

    JSONB columns arrive as TEXT on SQLite (and sometimes asyncpg). For
    values that look like JSON objects/arrays, attempt to re-parse so the
    archive contains structured JSON. A future schema change that adds a
    plain TEXT column whose first non-whitespace char is `{` or `[` would
    get round-tripped through json.loads — currently no such column exists,
    but worth knowing.
    """
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, str):
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
    """Archives conversation records to S3 as JSONL.

    Encryption settings are resolved once at construction so misconfigured
    deployments fail fast at startup rather than after a full DB scan.

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix.
        batch_size: Calls fetched per batch (purger drives the loop).
        encryption_mode: One of `AES256`, `aws:kms`, or `bucket-default`.
            `bucket-default` omits the SSE header so bucket policy applies —
            use this when bucket policy mandates a mode that conflicts with
            `AES256`.
        kms_key_id: Required when encryption_mode is `aws:kms`. An empty
            value would silently fall back to the AWS-managed default key,
            so we reject it instead.
        s3_client: Optional pre-built boto3 S3 client (for testing). If
            None, a client is created lazily using `boto3.client("s3")`.

    Raises:
        ValueError: If encryption settings are invalid.
    """

    # SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999. _fetch_children
    # binds one positional placeholder per call_id in an IN clause, so the
    # batch size must stay strictly below that to leave headroom for any
    # additional placeholders added in future query forms.
    _MAX_BATCH_SIZE = 900

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "luthien-archive/",
        batch_size: int = 100,
        encryption_mode: str = "AES256",
        kms_key_id: str = "",
        s3_client: Any = None,
    ) -> None:
        """Initialize archiver. Validates encryption + sizing up-front."""
        if encryption_mode not in VALID_ENCRYPTION_MODES:
            raise ValueError(
                f"encryption_mode={encryption_mode!r} is not valid. "
                f"Must be one of: {sorted(VALID_ENCRYPTION_MODES)}"
            )
        if encryption_mode == "aws:kms" and not kms_key_id:
            raise ValueError(
                "encryption_mode='aws:kms' requires kms_key_id to be set. "
                "Leaving it unset silently falls back to the AWS-managed default key, "
                "which is weaker than an explicitly configured customer-managed KMS key."
            )
        if batch_size < 1 or batch_size > self._MAX_BATCH_SIZE:
            raise ValueError(
                f"batch_size={batch_size} out of range. Must be in [1, {self._MAX_BATCH_SIZE}]. "
                "Upper bound is set by SQLite's SQLITE_MAX_VARIABLE_NUMBER (default 999); "
                "the IN clause for child-table fetches binds one placeholder per call_id."
            )
        # If the operator didn't inject an S3 client, probe that boto3 is
        # importable now rather than at first archive-run, weeks after
        # deployment. Otherwise a mistyped extras_require survives until
        # the first purge tries to upload.
        if s3_client is None:
            try:
                import boto3  # type: ignore[import-untyped]  # noqa: F401, PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "ARCHIVE_S3_BUCKET is set but boto3 is not installed. Install it with: pip install boto3"
                ) from exc
        # Normalize prefix:
        #   - Strip leading slashes. S3 tolerates `s3://bucket//foo/key` but
        #     it's a foot-gun for Athena partition projection (an empty
        #     leading segment confuses partition discovery).
        #   - Append trailing slash if missing. A non-empty prefix without
        #     `/` silently produces keys like `fooDATE/...` instead of
        #     `foo/DATE/...`.
        # Empty prefix (root of bucket) is preserved as-is.
        prefix = prefix.lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        self.bucket = bucket
        self.prefix = prefix
        self.batch_size = batch_size
        self._encryption_mode = encryption_mode
        self._kms_key_id = kms_key_id
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

        Format: ``{prefix}{run-YYYY-MM-DD}/cutoff-{cutoff-YYYY-MM-DD}-{timestamp}-{run_id}-{batch:04d}.jsonl``

        Partition by *run date* (when the archive happened), not cutoff date.
        Operators expect ``s3://bucket/luthien-archive/<today>/`` to contain
        what was archived today. The cutoff date is encoded inside the
        filename for restore queries that need it.

        run_id is shared across batches in one purge; batch_index increments
        per batch. Together they make object listing / restore deterministic.
        """
        now = datetime.now(UTC)
        run_date = now.strftime("%Y-%m-%d")
        cutoff_date = cutoff.strftime("%Y-%m-%d")
        ts_str = now.strftime("%Y%m%dT%H%M%SZ")
        return f"{self.prefix}{run_date}/cutoff-{cutoff_date}-{ts_str}-{run_id}-{batch_index:04d}.jsonl"

    def _build_put_kwargs(self, key: str, body: bytes) -> dict[str, Any]:
        """Build kwargs for `s3.put_object`, honouring the configured encryption mode."""
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": "application/x-ndjson",
        }
        if self._encryption_mode == "bucket-default":
            return kwargs
        kwargs["ServerSideEncryption"] = self._encryption_mode
        if self._encryption_mode == "aws:kms":
            kwargs["SSEKMSKeyId"] = self._kms_key_id
        return kwargs

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

    async def _build_batch_records(self, db_conn: Any, call_rows: list[Any]) -> list[str]:
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

    async def fetch_batch(
        self,
        *,
        db_conn: Any,
        cutoff: datetime,
        last_call_id: str | None,
    ) -> tuple[bytes, list[str], bool]:
        """Fetch one batch of calls + descendants and serialize to JSONL.

        DB-only; does not touch S3. Splitting fetch from upload lets the
        purger release the connection back to the pool *before* the
        seconds-to-minutes S3 PUT, so a slow upload doesn't pin a pool
        slot the gateway needs for serving requests.

        The ``last_call_id`` cursor is not strictly required for
        correctness in the normal flow — the purger DELETEs each batch
        before the next ``fetch_batch`` call, so an offsetless query
        would also surface the next batch. The cursor is defensive
        against (a) a DELETE that did not actually delete (concurrent
        purger / replica) and (b) keeping the no-archive path
        symmetrical, where the same caveat applies.

        Cursor pagination assumption: rows aren't backdated. The query
        ``WHERE created_at < $cutoff AND call_id > $last`` would skip a
        row if a backdated insert with a smaller call_id arrived between
        batches. The codebase has no backdated-insert path; the next
        run's cutoff predicate would still pick it up.

        Returns:
            (jsonl_body, archived_call_ids, has_more). Empty list +
            ``has_more=False`` when no more rows match the cutoff.
        """
        call_rows = await self._fetch_call_batch(db_conn, cutoff, last_call_id)
        if not call_rows:
            return b"", [], False
        lines = await self._build_batch_records(db_conn, call_rows)
        # Trailing newline so every line — including the last — ends in \n.
        # Athena/Glue/Spark NDJSON loaders are stricter than json-per-line
        # parsers about this.
        body = ("\n".join(lines) + "\n").encode("utf-8")
        archived_ids = [row["call_id"] for row in call_rows]
        return body, archived_ids, len(call_rows) >= self.batch_size

    async def upload_batch(
        self,
        *,
        body: bytes,
        cutoff: datetime,
        run_id: str,
        batch_index: int,
        record_count: int,
    ) -> None:
        """Upload one batch of JSONL to S3.

        S3-only; does not touch the DB. The caller passes ``record_count``
        purely so the success log can include it.

        Raises:
            Exception: If the S3 upload fails. The caller should skip the
                DELETE for this batch and stop the run.
        """
        key = self._build_s3_key(cutoff, run_id, batch_index)
        put_kwargs = self._build_put_kwargs(key=key, body=body)
        await asyncio.to_thread(self._get_s3_client().put_object, **put_kwargs)
        logger.info(
            "Archived batch %d (%d records) to s3://%s/%s",
            batch_index,
            record_count,
            self.bucket,
            key,
        )

    @staticmethod
    def new_run_id() -> str:
        """Return a fresh run id used to group all batches from one purge.

        Uses the full uuid4 hex (32 chars / 128 bits). Truncating to 8 chars
        looks tidy in S3 keys but only gives 32 bits of collision space —
        under the multi-replica deployments tracked as a follow-up, two
        replicas starting in the same second with the same truncated id
        would silently overwrite each other's batch objects. The full hex
        keeps the cost ~zero (S3 keys are cheap) and removes the risk.
        """
        return uuid.uuid4().hex
