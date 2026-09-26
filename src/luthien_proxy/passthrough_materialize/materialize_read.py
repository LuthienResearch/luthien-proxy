"""Raw request-log selection and provider JSON parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.materialize_types import (
    CapturedTransaction,
    MaterializationFailed,
    RawCapturedTransaction,
)
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    fail,
    is_json_object,
)
from luthien_proxy.passthrough_materialize.payloads import JsonObject
from luthien_proxy.utils.db import DatabasePool, parse_db_ts


@dataclass(frozen=True, slots=True)
class _InvalidRequestLog(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


async def read_raw_transaction(
    db_pool: DatabasePool, transaction_id: str
) -> RawCapturedTransaction | MaterializationFailed:
    """Return inbound-preferred persisted data for one request-log transaction."""
    async with db_pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT request_body, response_body, response_status, session_id, user_id,
                   model, is_streaming, endpoint, error, started_at, completed_at
            FROM request_logs
            WHERE transaction_id = $1
            ORDER BY CASE direction WHEN 'inbound' THEN 0 ELSE 1 END, started_at
            """,
            transaction_id,
        )
    if not rows:
        return MaterializationFailed(transaction_id=transaction_id, reason="missing_request_logs")
    try:
        return _raw_transaction_from_rows(rows, transaction_id)
    except _InvalidRequestLog as error:
        return MaterializationFailed(transaction_id=transaction_id, reason=error.detail)


def parse_captured_transaction(raw: RawCapturedTransaction, endpoint: EligibleEndpoint) -> CapturedTransaction:
    """Parse selected provider bodies or raise a typed retryable normalizer error."""
    request_body = _parse_provider_body(raw.request_body, endpoint, raw.transaction_id, "request_body")
    response_body = _response_body(raw, endpoint)
    return CapturedTransaction(raw=raw, endpoint=endpoint, request_body=request_body, response_body=response_body)


def _raw_transaction_from_rows(rows: Sequence[Mapping[str, object]], transaction_id: str) -> RawCapturedTransaction:
    request_body = _optional_string(rows, "request_body")
    response_body = _optional_string(rows, "response_body")
    response_status = _optional_status(rows)
    started_at = _required_timestamp(rows, "started_at")
    return RawCapturedTransaction(
        transaction_id=transaction_id,
        request_body=request_body,
        response_body=response_body,
        response_status=response_status,
        session_id=_optional_string(rows, "session_id"),
        user_id=_optional_string(rows, "user_id"),
        model=_optional_string(rows, "model"),
        is_streaming=_is_streaming(rows),
        endpoint=_optional_string(rows, "endpoint"),
        error=_optional_string(rows, "error"),
        started_at=started_at,
        completed_at=_optional_timestamp(rows, "completed_at"),
    )


def _response_body(raw: RawCapturedTransaction, endpoint: EligibleEndpoint) -> JsonObject:
    if raw.response_body is not None:
        return _parse_provider_body(raw.response_body, endpoint, raw.transaction_id, "response_body")
    if raw.error is not None:
        return {"error": raw.error}
    fail(endpoint, raw.transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "response_body")


def _parse_provider_body(raw: str | None, endpoint: EligibleEndpoint, transaction_id: str, field: str) -> JsonObject:
    if raw is None:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, field)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_JSON, field)
    if is_json_object(parsed):
        return parsed
    fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, field)


def _first_value(rows: Sequence[Mapping[str, object]], column: str) -> object | None:
    for row in rows:
        value = row[column]
        if value is not None:
            return value
    return None


def _optional_string(rows: Sequence[Mapping[str, object]], column: str) -> str | None:
    value = _first_value(rows, column)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise _InvalidRequestLog(detail=f"invalid_{column}")


def _optional_status(rows: Sequence[Mapping[str, object]]) -> int | None:
    value = _first_value(rows, "response_status")
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise _InvalidRequestLog(detail="invalid_response_status")


def _is_streaming(rows: Sequence[Mapping[str, object]]) -> bool:
    value = _first_value(rows, "is_streaming")
    match value:
        case None | False | 0:
            return False
        case True | 1:
            return True
        case _:
            raise _InvalidRequestLog(detail="invalid_is_streaming")


def _required_timestamp(rows: Sequence[Mapping[str, object]], column: str) -> datetime:
    timestamp = _optional_timestamp(rows, column)
    if timestamp is not None:
        return timestamp
    raise _InvalidRequestLog(detail=f"missing_{column}")


def _optional_timestamp(rows: Sequence[Mapping[str, object]], column: str) -> datetime | None:
    value = _first_value(rows, column)
    if value is None:
        return None
    try:
        return parse_db_ts(value)
    except TypeError as error:
        raise _InvalidRequestLog(detail=f"invalid_{column}") from error


__all__ = ["parse_captured_transaction", "read_raw_transaction"]
