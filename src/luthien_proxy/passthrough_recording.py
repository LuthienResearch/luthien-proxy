"""Create passthrough request recorders with trusted attribution and optional materialization."""

from __future__ import annotations

from collections.abc import Mapping

from luthien_proxy.dependencies import Dependencies
from luthien_proxy.passthrough_materialize.materialize import materialize_transaction
from luthien_proxy.pipeline.session import (
    extract_user_id_from_authorization_header,
    extract_user_id_from_headers,
)
from luthien_proxy.request_log.recorder import RequestLogRecorder, create_recorder
from luthien_proxy.settings import get_settings


def create_passthrough_recorder(
    headers: Mapping[str, str], transaction_id: str, deps: Dependencies
) -> tuple[RequestLogRecorder, str | None, str | None]:
    """Return a recorder plus session and user identities derived from request headers."""
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    settings = get_settings()
    session_id = normalized_headers.get("x-session-id") or normalized_headers.get("x-luthien-session-id")
    user_id = extract_user_id_from_headers(
        normalized_headers, trust_header=settings.trust_user_id_header
    ) or extract_user_id_from_authorization_header(normalized_headers.get("authorization"))
    db_pool = deps.db_pool
    if settings.passthrough_materialize_enabled and db_pool is not None:

        async def on_commit(transaction_id: str) -> None:
            await materialize_transaction(db_pool, transaction_id)

        return (
            create_recorder(
                db_pool,
                transaction_id=transaction_id,
                enabled=deps.enable_request_logging,
                on_commit=on_commit,
            ),
            session_id,
            user_id,
        )
    return (
        create_recorder(db_pool, transaction_id=transaction_id, enabled=deps.enable_request_logging),
        session_id,
        user_id,
    )


__all__ = ["create_passthrough_recorder"]
