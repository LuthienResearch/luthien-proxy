"""SQLite request-log body serialization integration tests."""

from __future__ import annotations

import asyncio

import pytest

from luthien_proxy.request_log.recorder import RequestLogRecorder
from luthien_proxy.request_log.service import get_transaction_logs
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations

pytestmark = pytest.mark.sqlite_e2e


@pytest.mark.asyncio
async def test_nul_body_round_trips_as_replacement_character() -> None:
    """A logged transaction retains both rows when a body contains a NUL."""
    pool = DatabasePool("sqlite://:memory:")
    await check_migrations(pool)
    body = {
        "content": "prefix\x00suffix",
        "nested": [{"number": 7, "enabled": True, "empty": None}],
    }
    expected_body = {
        "content": "prefix\ufffdsuffix",
        "nested": [{"number": 7, "enabled": True, "empty": None}],
    }
    recorder = RequestLogRecorder(pool, "nul-body-transaction")
    recorder.record_inbound_request(
        method="POST",
        url="http://example.test/v1/messages",
        headers={},
        body=body,
    )
    recorder.record_inbound_response(status=200)
    recorder.record_outbound_request(body=body)
    recorder.record_outbound_response(status=200)

    try:
        recorder.flush()
        for _ in range(100):
            try:
                detail = await get_transaction_logs(pool, "nul-body-transaction")
            except ValueError:
                await asyncio.sleep(0.01)
                continue
            if detail.inbound is not None and detail.outbound is not None:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("request-log writer did not persist both transaction rows")

        assert detail.inbound is not None
        assert detail.outbound is not None
        assert detail.inbound.request_body == expected_body
        assert detail.outbound.request_body == expected_body
    finally:
        await pool.close()
