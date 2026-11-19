"""PostgreSQL integration coverage for event JSONB storage."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from luthien_proxy.observability.emitter import EventEmitter
from luthien_proxy.utils.db import DatabasePool

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_event_payload_with_nul_is_persisted_by_postgres() -> None:
    """JSONB storage keeps an identifiable replacement for NUL-containing event data."""
    db_url = os.environ["DATABASE_URL"]
    transaction_id = str(uuid4())
    pool = DatabasePool(db_url)
    emitter = EventEmitter(db_pool=pool, stdout_enabled=False)

    try:
        await emitter._write_db(
            transaction_id,
            "transaction.request_recorded",
            {"tool\x00output": {"content": "before\x00after"}},
            datetime.now(UTC),
        )

        async with pool.connection() as conn:
            event = await conn.fetchrow("SELECT payload FROM conversation_events WHERE call_id = $1", transaction_id)
        assert event is not None
        payload_json = event["payload"]
        assert isinstance(payload_json, str)
        assert json.loads(payload_json) == {
            "tool�output": {"content": "before�after"},
            "_sanitized": {"nul_replaced": 2},
        }
    finally:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM conversation_calls WHERE call_id = $1", transaction_id)
        await pool.close()
