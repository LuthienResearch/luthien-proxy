import aiosqlite
import pytest

pytestmark = pytest.mark.sqlite_e2e


@pytest.mark.asyncio
async def test_request_logs_has_session_id_and_agent(sqlite_db_path):
    async with aiosqlite.connect(sqlite_db_path) as db:
        cursor = await db.execute("PRAGMA table_info(request_logs)")
        columns = {row[1] for row in await cursor.fetchall()}
    assert "session_id" in columns, "session_id missing — migration 008 regression"
    assert "agent" in columns, "agent missing — migration 018 not applied"
