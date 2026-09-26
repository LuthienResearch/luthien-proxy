"""Transactional passthrough materialization entry point."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import assert_never

from opentelemetry import metrics

from luthien_proxy.passthrough_materialize.endpoints import (
    EndpointKind,
    ExcludedEndpoint,
    classify_endpoint,
)
from luthien_proxy.passthrough_materialize.gemini import (
    normalize_gemini_request,
    normalize_gemini_response,
)
from luthien_proxy.passthrough_materialize.materialize_read import (
    parse_captured_transaction,
    read_raw_transaction,
)
from luthien_proxy.passthrough_materialize.materialize_types import (
    CanonicalTransaction,
    CapturedTransaction,
    MaterializationFailed,
    MaterializationResult,
    RawCapturedTransaction,
    SkippedIneligible,
)
from luthien_proxy.passthrough_materialize.materialize_write import write_canonical_transaction
from luthien_proxy.passthrough_materialize.openai import (
    PassthroughNormalizeError,
    normalize_openai_chat_request,
    normalize_openai_chat_response,
    normalize_openai_responses_request,
    normalize_openai_responses_response,
)
from luthien_proxy.passthrough_materialize.openai_common import PassthroughNormalizeReason, fail
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalRequestInput,
    CanonicalResponseInput,
    build_request_event_payload,
    build_response_event_payload,
)
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)
_meter = metrics.get_meter("luthien_proxy.passthrough_materialize")
_materialization_failures = _meter.create_counter(
    "luthien.passthrough.materialization.failures",
    description="Passthrough transactions that could not be materialized and remain retryable.",
)


async def materialize_transaction(db_pool: DatabasePool, transaction_id: str) -> MaterializationResult:
    """Materialize captured passthrough logs into canonical conversation rows."""
    raw_or_failure = await read_raw_transaction(db_pool, transaction_id)
    match raw_or_failure:
        case MaterializationFailed() as failure:
            return _record_failure(failure)
        case RawCapturedTransaction() as raw:
            return await _materialize_raw_transaction(db_pool, raw)
        case unreachable:
            assert_never(unreachable)


async def _materialize_raw_transaction(db_pool: DatabasePool, raw: RawCapturedTransaction) -> MaterializationResult:
    if raw.endpoint is None:
        return _record_failure(MaterializationFailed(transaction_id=raw.transaction_id, reason="missing_endpoint"))
    classification = classify_endpoint(raw.endpoint)
    match classification:
        case ExcludedEndpoint():
            return SkippedIneligible(transaction_id=raw.transaction_id, endpoint=raw.endpoint)
        case endpoint:
            try:
                captured = parse_captured_transaction(raw, endpoint)
                transaction = _canonical_transaction(captured)
            except PassthroughNormalizeError as error:
                return _record_failure(
                    MaterializationFailed(transaction_id=raw.transaction_id, reason=error.reason.value)
                )
    return await write_canonical_transaction(db_pool, transaction)


def _canonical_transaction(captured: CapturedTransaction) -> CanonicalTransaction:
    raw = captured.raw
    request_input, response_input = _normalization_inputs(captured)
    final_model = response_input.final_model or request_input.final_model or raw.model
    request_payload = build_request_event_payload(
        replace(request_input, final_model=final_model, is_streaming=raw.is_streaming)
    )
    response_payload = build_response_event_payload(
        replace(response_input, final_model=final_model, is_streaming=raw.is_streaming)
    )
    response_status = _response_status(captured)
    return CanonicalTransaction(
        captured=captured,
        request_payload=request_payload,
        response_payload=response_payload,
        final_model=final_model,
        request_at=raw.started_at,
        response_at=_response_timestamp(raw),
        status="error" if raw.error is not None or response_status >= 400 else "completed",
    )


def _normalization_inputs(captured: CapturedTransaction) -> tuple[CanonicalRequestInput, CanonicalResponseInput]:
    endpoint = captured.endpoint
    raw = captured.raw
    status = _response_status(captured)
    match endpoint.kind:
        case EndpointKind.OPENAI_CHAT_COMPLETIONS:
            return (
                normalize_openai_chat_request(endpoint, captured.request_body, transaction_id=raw.transaction_id),
                normalize_openai_chat_response(
                    endpoint,
                    captured.response_body,
                    request_is_streaming=raw.is_streaming,
                    http_status=status,
                    transaction_id=raw.transaction_id,
                ),
            )
        case EndpointKind.OPENAI_RESPONSES:
            return (
                normalize_openai_responses_request(endpoint, captured.request_body, transaction_id=raw.transaction_id),
                normalize_openai_responses_response(
                    endpoint,
                    captured.response_body,
                    request_is_streaming=raw.is_streaming,
                    http_status=status,
                    transaction_id=raw.transaction_id,
                ),
            )
        case EndpointKind.GEMINI_GENERATE_CONTENT | EndpointKind.GEMINI_STREAM_GENERATE_CONTENT:
            return (
                normalize_gemini_request(endpoint, captured.request_body, transaction_id=raw.transaction_id),
                normalize_gemini_response(
                    endpoint,
                    captured.response_body,
                    request_is_streaming=raw.is_streaming,
                    http_status=status,
                    transaction_id=raw.transaction_id,
                ),
            )
        case unreachable:
            assert_never(unreachable)


def _response_status(captured: CapturedTransaction) -> int:
    status = captured.raw.response_status
    if status is not None:
        return status
    if captured.raw.error is not None:
        return 502
    fail(
        captured.endpoint,
        captured.raw.transaction_id,
        PassthroughNormalizeReason.MISSING_REQUIRED_FIELD,
        "response_status",
    )


def _response_timestamp(raw: RawCapturedTransaction) -> datetime:
    completed_at = raw.completed_at
    if completed_at is not None and completed_at > raw.started_at:
        return completed_at
    return raw.started_at + timedelta(microseconds=1)


def _record_failure(failure: MaterializationFailed) -> MaterializationFailed:
    _materialization_failures.add(1, {"reason": failure.reason})
    logger.warning("Passthrough materialization failed for %s: %s", failure.transaction_id, failure.reason)
    return failure


__all__ = ["materialize_transaction"]
