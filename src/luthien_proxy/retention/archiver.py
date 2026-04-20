"""S3 archival for conversation data before purge.

Fetches conversation_calls rows older than a cutoff datetime, serializes them
to JSONL, and uploads to S3. Raises on S3 errors so the caller (purger) can
skip deletion when archival fails.

boto3 is an optional dependency — imported lazily. If ARCHIVE_S3_BUCKET is
unset, this module is never instantiated and boto3 is never imported.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def _serialize_value(v: Any) -> Any:
    """Convert non-JSON-serializable values to JSON-safe equivalents."""
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a DB row (asyncpg Record or dict-like) to a plain dict."""
    if hasattr(row, "keys"):
        return {k: _serialize_value(row[k]) for k in row.keys()}
    return {k: _serialize_value(v) for k, v in dict(row).items()}


class S3ConversationArchiver:
    """Archives conversation_calls rows to S3 as JSONL before purge.

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix (default: "luthien-archive/").
        s3_client: Optional pre-built boto3 S3 client (for testing). If None,
            a client is created lazily using boto3.client("s3").
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "luthien-archive/",
        s3_client: Any = None,
    ) -> None:
        """Initialize archiver with S3 bucket, key prefix, and optional pre-built client."""
        self.bucket = bucket
        self.prefix = prefix
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

        Format: {prefix}{YYYY-MM-DD}/{timestamp}.jsonl
        """
        date_str = cutoff.strftime("%Y-%m-%d")
        ts_str = cutoff.strftime("%Y%m%dT%H%M%SZ")
        return f"{self.prefix}{date_str}/{ts_str}.jsonl"

    async def archive_calls(self, *, db_conn: Any, cutoff: datetime) -> None:
        """Fetch rows older than cutoff and upload to S3 as JSONL.

        Args:
            db_conn: An active DB connection (ConnectionProtocol).
            cutoff: Rows with created_at < cutoff will be archived.

        Raises:
            Exception: If S3 upload fails. The caller should catch this and
                skip deletion to avoid data loss.
        """
        rows = await db_conn.fetch(
            "SELECT * FROM conversation_calls WHERE created_at < $1 ORDER BY created_at",
            cutoff,
        )

        if not rows:
            logger.debug("No conversation_calls to archive before %s", cutoff.isoformat())
            return

        logger.info("Archiving %d conversation_calls to s3://%s", len(rows), self.bucket)

        jsonl_lines = [json.dumps(_row_to_dict(row)) for row in rows]
        body = "\n".join(jsonl_lines).encode("utf-8")
        key = self._build_s3_key(cutoff)

        s3 = self._get_s3_client()
        await asyncio.to_thread(
            s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/x-ndjson",
        )
        logger.info("Archived %d rows to s3://%s/%s", len(rows), self.bucket, key)
