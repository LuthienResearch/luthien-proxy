import sqlite3
from unittest.mock import patch

import pytest

from luthien_proxy.perf.db import (
    drop_perf_db,
    ensure_perf_isolation,
    get_perf_db_url,
    migrate_perf_db,
)


def test_ensure_perf_isolation_rejects_local_db():
    with pytest.raises(RuntimeError, match="isolation"):
        ensure_perf_isolation("sqlite:///~/.luthien/local.db")


def test_ensure_perf_isolation_accepts_perf_db():
    ensure_perf_isolation("sqlite:////Users/test/.luthien/perf.db")


def test_ensure_perf_isolation_rejects_postgres_without_perf_test():
    with pytest.raises(RuntimeError, match="isolation"):
        ensure_perf_isolation("postgresql://user:pass@localhost/luthien")


def test_ensure_perf_isolation_accepts_postgres_with_perf_test():
    ensure_perf_isolation("postgresql://user:pass@localhost/luthien?options=-csearch_path=perf_test")


def test_get_perf_db_url_sqlite():
    url = get_perf_db_url("sqlite")
    assert url.startswith("sqlite:///")
    assert "perf.db" in url
    assert "local.db" not in url


def test_drop_perf_db_idempotent(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        drop_perf_db("sqlite")
        drop_perf_db("sqlite")


def test_migrate_perf_db_creates_tables(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        migrate_perf_db("sqlite")

    perf_db = tmp_path / ".luthien" / "perf.db"
    assert perf_db.exists()

    conn = sqlite3.connect(str(perf_db))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {row[0] for row in rows}
        assert "conversation_events" in table_names
        assert "conversation_calls" in table_names
    finally:
        conn.close()
