"""Data models for request/response logging API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RequestLogEntry(BaseModel):
    """A single request/response log entry."""

    id: str
    transaction_id: str
    session_id: str | None = None
    direction: str
    http_method: str | None = None
    url: str | None = None
    request_headers: dict[str, str] | None = None
    request_body: dict[str, Any] | None = None
    response_status: int | None = None
    response_headers: dict[str, str] | None = None
    response_body: dict[str, Any] | None = None
    started_at: str
    completed_at: str | None = None
    duration_ms: float | None = None
    model: str | None = None
    is_streaming: bool = False
    endpoint: str | None = None
    error: str | None = None


class RequestLogListResponse(BaseModel):
    """Paginated list of request log entries."""

    logs: list[RequestLogEntry]
    total: int
    limit: int
    offset: int


class RequestLogDetailResponse(BaseModel):
    """All log entries (inbound + outbound) for a single transaction."""

    transaction_id: str
    session_id: str | None = None
    inbound: RequestLogEntry | None = None
    outbound: RequestLogEntry | None = None


__all__ = [
    "RequestLogEntry",
    "RequestLogListResponse",
    "RequestLogDetailResponse",
]
