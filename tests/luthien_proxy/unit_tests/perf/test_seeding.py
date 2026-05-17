import sqlite3
from unittest.mock import patch

import pytest

from luthien_proxy.perf.seeding import seed_sami_like, seed_sessions

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def isolated_home(tmp_path):
    (tmp_path / ".luthien").mkdir()
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield tmp_path


def _db(home):
    return sqlite3.connect(str(home / ".luthien" / "perf.db"))


def test_seed_100_row_counts(isolated_home):
    report = seed_sessions("sqlite", tier=100)

    assert report.total_sessions == 100
    assert report.total_rows > 0

    conn = _db(isolated_home)
    try:
        (n_calls,) = conn.execute("SELECT COUNT(*) FROM conversation_calls").fetchone()
        (n_events,) = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()
    finally:
        conn.close()

    assert n_calls > 0
    assert n_events == 2 * n_calls
    assert report.total_rows == n_calls + n_events


def test_seed_prefix(isolated_home):
    seed_sessions("sqlite", tier=100)

    conn = _db(isolated_home)
    try:
        rows = conn.execute("SELECT DISTINCT session_id FROM conversation_events").fetchall()
    finally:
        conn.close()

    session_ids = [r[0] for r in rows]
    assert len(session_ids) == 100
    for sid in session_ids:
        assert sid.startswith("perf-seed-100-"), sid


def test_seed_idempotent(isolated_home):
    from luthien_proxy.perf.db import drop_perf_db

    seed_sessions("sqlite", tier=100)
    conn = _db(isolated_home)
    try:
        (n_calls_1,) = conn.execute("SELECT COUNT(*) FROM conversation_calls").fetchone()
        (n_events_1,) = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()
    finally:
        conn.close()

    drop_perf_db("sqlite")
    seed_sessions("sqlite", tier=100)
    conn = _db(isolated_home)
    try:
        (n_calls_2,) = conn.execute("SELECT COUNT(*) FROM conversation_calls").fetchone()
        (n_events_2,) = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()
    finally:
        conn.close()

    assert n_calls_1 == n_calls_2
    assert n_events_1 == n_events_2


def test_sami_like_78_sessions(isolated_home):
    report = seed_sami_like("sqlite")

    assert report.total_sessions == 78

    conn = _db(isolated_home)
    try:
        (n_sessions,) = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM conversation_events WHERE session_id LIKE 'perf-seed-sami-%'"
        ).fetchone()
    finally:
        conn.close()

    assert n_sessions == 78


def test_sami_like_442_msg_session(isolated_home):
    report = seed_sami_like("sqlite")

    assert report.biggest_session_message_count >= 442

    conn = _db(isolated_home)
    try:
        (n_calls,) = conn.execute(
            "SELECT COUNT(*) FROM conversation_calls WHERE session_id = 'perf-seed-sami-442msg'"
        ).fetchone()
    finally:
        conn.close()

    assert n_calls == 442


def test_payload_sizes():
    from luthien_proxy.perf.seeding import _req_payload, _resp_payload

    req = _req_payload("perf-seed-test-0001", 0)
    resp = _resp_payload("perf-seed-test-0001", 0)
    assert 4 * 1024 <= len(req) <= 6 * 1024, f"req payload {len(req)} bytes not in [4KB, 6KB]"
    assert 18 * 1024 <= len(resp) <= 22 * 1024, f"resp payload {len(resp)} bytes not in [18KB, 22KB]"


def test_seeded_db_has_same_indexes_as_migrated_db(isolated_home):
    from luthien_proxy.perf.db import drop_perf_db, migrate_perf_db

    migrate_perf_db("sqlite")
    conn = _db(isolated_home)
    try:
        migrated_indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        }
    finally:
        conn.close()

    drop_perf_db("sqlite")
    seed_sessions("sqlite", tier=10)
    conn = _db(isolated_home)
    try:
        seeded_indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        }
    finally:
        conn.close()

    assert seeded_indexes == migrated_indexes, (
        f"Seeded DB indexes differ from migrated DB.\n"
        f"Missing after reseed: {migrated_indexes - seeded_indexes}\n"
        f"Extra after reseed: {seeded_indexes - migrated_indexes}"
    )


def test_seed_sqlite_recreates_indexes_after_rollback(isolated_home):
    import sqlite3 as _sqlite3

    from luthien_proxy.perf.db import get_perf_db_url, migrate_perf_db
    from luthien_proxy.perf.seeding import _INDEX_STMTS, _sqlite_path

    migrate_perf_db("sqlite")
    db_path = _sqlite_path(get_perf_db_url("sqlite"))

    conn = _sqlite3.connect(str(db_path), isolation_level=None)
    try:
        for idx in (
            "idx_conversation_events_type",
            "idx_conversation_events_created",
            "idx_conversation_events_call_created",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")

        for stmt in _INDEX_STMTS:
            conn.execute(stmt)
    finally:
        conn.close()

    after = _sqlite3.connect(str(db_path))
    try:
        indexes = {
            row[0]
            for row in after.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        }
    finally:
        after.close()

    assert len(indexes) >= len(_INDEX_STMTS), "All indexes should be recreated"


def test_seed_sessions_raises_if_rows_already_exist(isolated_home):
    seed_sessions("sqlite", tier=10)
    with pytest.raises(RuntimeError, match="already exist"):
        seed_sessions("sqlite", tier=10)


def test_seeding_refuses_dev_db(tmp_path):
    with patch(
        "luthien_proxy.perf.seeding.get_perf_db_url",
        return_value=f"sqlite:///{tmp_path}/local.db",
    ):
        with pytest.raises(RuntimeError, match="isolation"):
            seed_sessions("sqlite", tier=10)
