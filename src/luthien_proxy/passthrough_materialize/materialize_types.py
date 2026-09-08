"""Typed values used by passthrough materialization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalRequestPayload,
    CanonicalResponsePayload,
    JsonObject,
)


@dataclass(frozen=True, slots=True)
class Materialized:
    """A transaction whose canonical rows were written."""

    transaction_id: str
    status: Literal["materialized"] = "materialized"


@dataclass(frozen=True, slots=True)
class AlreadyMaterialized:
    """A transaction that already has its canonical request event."""

    transaction_id: str
    status: Literal["already_materialized"] = "already_materialized"


@dataclass(frozen=True, slots=True)
class SkippedIneligible:
    """A transaction whose endpoint intentionally has no canonical representation."""

    transaction_id: str
    endpoint: str
    status: Literal["skipped_ineligible"] = "skipped_ineligible"


@dataclass(frozen=True, slots=True)
class MaterializationFailed:
    """A retryable materialization failure that did not write canonical rows."""

    transaction_id: str
    reason: str
    status: Literal["failed"] = "failed"


type MaterializationResult = Materialized | AlreadyMaterialized | SkippedIneligible | MaterializationFailed


@dataclass(frozen=True, slots=True)
class ReconcileStats:
    """Outcome counts for one bounded passthrough reconciliation sweep."""

    materialized: int = 0
    already_materialized: int = 0
    skipped_ineligible: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class RawCapturedTransaction:
    """Inbound-preferred persisted request-log data before provider parsing."""

    transaction_id: str
    request_body: str | None
    response_body: str | None
    response_status: int | None
    session_id: str | None
    user_id: str | None
    model: str | None
    is_streaming: bool
    endpoint: str | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class CapturedTransaction:
    """Provider JSON parsed from a raw capture and ready for normalization."""

    raw: RawCapturedTransaction
    endpoint: EligibleEndpoint
    request_body: JsonObject
    response_body: JsonObject


@dataclass(frozen=True, slots=True)
class CanonicalTransaction:
    """Normalized payloads and timestamps ready for one atomic DB write."""

    captured: CapturedTransaction
    request_payload: CanonicalRequestPayload
    response_payload: CanonicalResponsePayload
    final_model: str | None
    request_at: datetime
    response_at: datetime
    status: Literal["completed", "error"]


__all__ = [
    "AlreadyMaterialized",
    "CanonicalTransaction",
    "CapturedTransaction",
    "MaterializationFailed",
    "MaterializationResult",
    "Materialized",
    "RawCapturedTransaction",
    "ReconcileStats",
    "SkippedIneligible",
]
