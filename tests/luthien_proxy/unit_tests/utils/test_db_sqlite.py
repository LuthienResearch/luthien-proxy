from datetime import datetime, timezone
from pathlib import Path

import pytest

from luthien_proxy.utils.db_sqlite import (
    _convert_arg,
    _translate_params,
    create_sqlite_pool,
    is_sqlite_url,
    parse_sqlite_url,
)


class TestTranslateParams:
    def test_basic_dollar_params(self):
        query = "SELECT * FROM t WHERE a = $1 AND b = $2"
        translated, args = _translate_params(query, ("x", "y"))
        assert translated == "SELECT * FROM t WHERE a = ? AND b = ?"
        assert args == ("x", "y")

    def test_high_numbered_params(self):
        query = "INSERT INTO t VALUES ($1, $2, $10, $11)"
        translated, _ = _translate_params(query, tuple(range(11)))
        assert "$" not in translated
        assert translated.count("?") == 4

    def test_strips_type_casts(self):
        query = "SELECT $1::jsonb, $2::text, $3::int"
        translated, _ = _translate_params(query, (1, 2, 3))
        assert "::" not in translated

    def test_strips_array_casts(self):
        query = "ARRAY[]::text[]"
        translated, _ = _translate_params(query, ())
        assert "::" not in translated

    def test_least_to_min(self):
        query = "SET created_at = LEAST(created_at, $1)"
        translated, _ = _translate_params(query, ("ts",))
        assert "MIN(" in translated
        assert "LEAST(" not in translated

    def test_to_timestamp(self):
        query = "to_timestamp($1)"
        translated, _ = _translate_params(query, (123.0,))
        assert "datetime(?, 'unixepoch')" in translated
        # Wrapped in replace(..., ' ', 'T') so the result matches the "T"
        # form _convert_arg produces for raw `datetime` binds (see #806
        # follow-up: request_logs.started_at/completed_at vs after/before).
        assert translated.startswith("replace(datetime(?, 'unixepoch'), ' ', 'T')")

    def test_now_to_datetime(self):
        query = "INSERT INTO t (id, ts) VALUES ($1, NOW())"
        translated, _ = _translate_params(query, (1,))
        assert "datetime('now')" in translated
        assert "NOW()" not in translated

    def test_now_case_insensitive(self):
        query = "VALUES (1, now())"
        translated, _ = _translate_params(query, ())
        assert "datetime('now')" in translated
        assert "now()" not in translated

    def test_now_in_on_conflict(self):
        query = (
            "INSERT INTO current_policy (id, policy_class_ref, config, enabled_at, enabled_by) "
            "VALUES (1, $1, $2, NOW(), $3) "
            "ON CONFLICT (id) DO UPDATE SET enabled_at = EXCLUDED.enabled_at"
        )
        translated, _ = _translate_params(query, ("ref", "{}", "admin"))
        assert "datetime('now')" in translated
        assert "NOW()" not in translated

    def test_ilike_to_like(self):
        query = "WHERE col ILIKE '%foo%'"
        translated, _ = _translate_params(query, ())
        assert " LIKE " in translated
        assert " ILIKE " not in translated

    def test_no_params(self):
        query = "SELECT 1"
        translated, args = _translate_params(query, ())
        assert translated == "SELECT 1"
        assert args == ()

    def test_positional_reuse_duplicates_arg(self):
        # `$8, $8` must expand to `?, ?` with the arg repeated, matching asyncpg's
        # positional-reuse semantics.
        query = "INSERT INTO t VALUES ($1, $2, $2)"
        translated, args = _translate_params(query, ("a", "b"))
        assert translated.count("?") == 3
        assert args == ("a", "b", "b")

    def test_positional_reuse_out_of_order(self):
        query = "INSERT INTO t VALUES ($2, $1, $2, $1)"
        translated, args = _translate_params(query, ("x", "y"))
        assert translated.count("?") == 4
        assert args == ("y", "x", "y", "x")

    def test_positional_reuse_with_high_numbers(self):
        query = "VALUES ($1, $2, $10, $10)"
        translated, args = _translate_params(query, tuple(range(11)))
        assert translated.count("?") == 4
        # $1, $2, $10, $10 → args[0], args[1], args[9], args[9]
        assert args == (0, 1, 9, 9)

    def test_rejects_dollar_n_inside_single_quoted_literal(self):
        # Reproducer for the S1 regression: pre-fix this silently corrupted
        # bind order; between fixes it raised IndexError. Post-fix it raises
        # a legible ValueError before any substitution happens.
        query = "SELECT '$5' FROM t WHERE x = $1"
        with pytest.raises(ValueError, match=r"\$N-looking token"):
            _translate_params(query, ("x",))

    def test_rejects_dollar_n_inside_literal_even_if_n_is_in_range(self):
        # The pre-fix regex would rewrite the '$1' inside the literal to '?',
        # producing 'SELECT ?' and then binding the user's "x" into the literal
        # position. That's silent SQL corruption, not a crash — catch it.
        query = "SELECT '$1' FROM t WHERE x = $1"
        with pytest.raises(ValueError, match=r"\$N-looking token"):
            _translate_params(query, ("x",))

    def test_rejects_dollar_n_inside_double_quoted_identifier(self):
        query = 'SELECT "$2" FROM t WHERE x = $1'
        with pytest.raises(ValueError, match=r"\$N-looking token"):
            _translate_params(query, ("x",))

    def test_accepts_escaped_quote_inside_literal_with_no_dollar_n(self):
        # Doubled single quotes escape inside SQL string literals; the literal
        # here contains 'o''clock' and no $N token, so translation must succeed.
        query = "SELECT 'o''clock' FROM t WHERE x = $1"
        translated, args = _translate_params(query, ("x",))
        assert translated == "SELECT 'o''clock' FROM t WHERE x = ?"
        assert args == ("x",)

    def test_rejects_dollar_zero_placeholder(self):
        # `$0` would otherwise silently map to args[-1] via Python negative
        # indexing, corrupting the bind. Guard in the substitution callback
        # raises before translation completes.
        query = "SELECT * FROM t WHERE a = $0"
        with pytest.raises(ValueError, match=r"Invalid parameter placeholder \$0"):
            _translate_params(query, ("x",))

    def test_rejects_out_of_range_dollar_n_with_descriptive_error(self):
        # `$3` with only 2 args should raise a descriptive ValueError naming
        # both the bad placeholder and the arg count, not a bare IndexError.
        query = "SELECT * FROM t WHERE a = $1 AND b = $2 AND c = $3"
        with pytest.raises(ValueError, match=r"Parameter \$3 exceeds number of provided arguments \(2\)"):
            _translate_params(query, ("x", "y"))

    def test_dollar_n_in_line_comment_is_substituted(self):
        # Documenting current behavior: `--` line comments are NOT parsed, so a
        # $N inside a comment gets rewritten. This is usually harmless (the
        # comment just gets a `?` in it) but is surprising; if this bites,
        # revisit and add comment-stripping.
        query = "SELECT $1 -- see $2 below\nFROM t"
        translated, args = _translate_params(query, ("a", "b"))
        assert translated.count("?") == 2
        assert args == ("a", "b")

    def test_dollar_n_in_block_comment_is_substituted(self):
        # Documenting current behavior: `/* */` block comments are NOT parsed, so a
        # $N inside a comment gets rewritten. This is usually harmless (the
        # comment just gets a `?` in it) but is surprising; if this bites,
        # revisit and add comment-stripping.
        query = "SELECT $1 /* see $2 here */ FROM t"
        translated, args = _translate_params(query, ("a", "b"))
        assert translated.count("?") == 2
        assert args == ("a", "b")


class TestConvertArg:
    def test_bool_to_int(self):
        assert _convert_arg(True) == 1
        assert _convert_arg(False) == 0

    def test_dict_to_json(self):
        result = _convert_arg({"key": "val"})
        assert result == '{"key": "val"}'

    def test_list_to_json(self):
        result = _convert_arg([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_passthrough(self):
        assert _convert_arg(42) == 42
        assert _convert_arg("hello") == "hello"
        assert _convert_arg(None) is None

    def test_datetime_serialized_as_isoformat_t_separator(self):
        # Root-cause guard: stdlib sqlite3's legacy datetime adapter serializes
        # with isoformat(" ") (space separator). The app stores/compares
        # created_at as ISO-8601 with a "T" (parse_db_ts + `created_at < $ts`
        # range filters). A space-separated value sorts BEFORE the "T" form
        # (0x20 < 0x54), so a turn's own event satisfied
        # `created_at < own_ts.isoformat()` and zeroed the history request-message
        # delta on SQLite. Binding datetimes as isoformat() keeps them comparable.
        value = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)
        assert _convert_arg(value) == "2026-07-11T10:00:00+00:00"
        assert "T" in str(_convert_arg(value))


class TestParseSqliteUrl:
    def test_absolute_path(self):
        assert parse_sqlite_url("sqlite:////tmp/test.db") == "/tmp/test.db"

    def test_relative_path(self):
        assert parse_sqlite_url("sqlite:///./data/test.db") == "./data/test.db"

    def test_memory(self):
        assert parse_sqlite_url("sqlite://:memory:") == ":memory:"

    def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid SQLite URL"):
            parse_sqlite_url("postgresql://localhost/test")


class TestIsSqliteUrl:
    def test_sqlite_url(self):
        assert is_sqlite_url("sqlite:///test.db") is True

    def test_postgres_url(self):
        assert is_sqlite_url("postgresql://localhost/test") is False

    def test_empty(self):
        assert is_sqlite_url("") is False


class TestSqlitePool:
    @pytest.mark.asyncio
    async def test_create_and_query_memory_db(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
            await pool.execute("INSERT INTO test (id, name) VALUES ($1, $2)", 1, "alice")

            rows = await pool.fetch("SELECT * FROM test WHERE id = $1", 1)
            assert len(rows) == 1
            assert rows[0]["id"] == 1
            assert rows[0]["name"] == "alice"

            row = await pool.fetchrow("SELECT * FROM test WHERE id = $1", 1)
            assert row is not None
            assert row["name"] == "alice"

            row_missing = await pool.fetchrow("SELECT * FROM test WHERE id = $1", 999)
            assert row_missing is None
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_connection_context_manager(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (val TEXT)")
            async with pool.acquire() as conn:
                await conn.execute("INSERT INTO t (val) VALUES ($1)", "test")
                rows = await conn.fetch("SELECT val FROM t")
                assert len(rows) == 1
                assert rows[0]["val"] == "test"
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_on_conflict_do_nothing(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
            await pool.execute("INSERT INTO t (id, val) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING", 1, "first")
            await pool.execute("INSERT INTO t (id, val) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING", 1, "second")

            row = await pool.fetchrow("SELECT val FROM t WHERE id = $1", 1)
            assert row is not None
            assert row["val"] == "first"
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_on_conflict_do_update(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
            await pool.execute(
                "INSERT INTO t (id, val) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET val = EXCLUDED.val",
                1,
                "first",
            )
            await pool.execute(
                "INSERT INTO t (id, val) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET val = EXCLUDED.val",
                1,
                "second",
            )

            row = await pool.fetchrow("SELECT val FROM t WHERE id = $1", 1)
            assert row is not None
            assert row["val"] == "second"
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_bool_conversion(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, flag INTEGER)")
            await pool.execute("INSERT INTO t (id, flag) VALUES ($1, $2)", 1, True)

            row = await pool.fetchrow("SELECT flag FROM t WHERE id = $1", 1)
            assert row is not None
            assert row["flag"] == 1
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_json_storage(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data TEXT)")
            await pool.execute("INSERT INTO t (id, data) VALUES ($1, $2)", 1, '{"key": "val"}')

            row = await pool.fetchrow("SELECT data FROM t WHERE id = $1", 1)
            assert row is not None
            assert row["data"] == '{"key": "val"}'
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_fetchval(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
            await pool.execute("INSERT INTO t (id, name) VALUES ($1, $2)", 1, "alice")

            async with pool.acquire() as conn:
                val = await conn.fetchval("SELECT COUNT(*) FROM t")
                assert val == 1

                val_none = await conn.fetchval("SELECT name FROM t WHERE id = $1", 999)
                assert val_none is None
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_datetime_bind_comparable_with_isoformat_string(self):
        # Regression: a datetime bind must round-trip as ISO-8601 with a "T"
        # separator so SQL range filters comparing `created_at < $ts` (where
        # $ts is a Python .isoformat() string) behave correctly. stdlib sqlite3's
        # legacy adapter stored a space separator, which sorts before the "T"
        # form (0x20 < 0x54); a row's own timestamp then satisfied
        # `created_at < own_ts.isoformat()`, silently corrupting history
        # pagination, session-search time ranges, and retention cutoffs.
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, created_at TEXT)")
            ts = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)
            await pool.execute("INSERT INTO t (id, created_at) VALUES ($1, $2)", 1, ts)

            stored = await pool.fetchrow("SELECT created_at FROM t WHERE id = $1", 1)
            assert stored is not None
            assert stored["created_at"] == "2026-07-11T10:00:00+00:00"

            # A strict `< own timestamp` filter must exclude the row itself.
            rows = await pool.fetch("SELECT id FROM t WHERE created_at < $1", ts.isoformat())
            assert [row["id"] for row in rows] == []
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_to_timestamp_write_comparable_with_datetime_bind_filter(self):
        # Regression: request_logs.started_at/completed_at are written via
        # to_timestamp(?) (a float unix-epoch bind, not a `datetime` object),
        # while request_log/service.py's after/before filters bind a raw
        # `datetime` (parsed via `datetime.fromisoformat`). SQLite's native
        # datetime(?, 'unixepoch') emits a space separator; a raw `datetime`
        # bind emits "T" via `_convert_arg`. Without the replace(...) wrap on
        # the to_timestamp(?) translation, a same-day `after`/`before` bound
        # would silently match nothing (>=) or everything (<), because ' '
        # (0x20) sorts before 'T' (0x54) regardless of actual time-of-day.
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await pool.execute("CREATE TABLE request_logs (id INTEGER PRIMARY KEY, started_at TEXT)")
            # Row started at noon UTC on 2026-08-10, written the same way
            # recorder.py does: a float unix-epoch bind through to_timestamp(?).
            noon = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
            await pool.execute(
                "INSERT INTO request_logs (id, started_at) VALUES ($1, to_timestamp($2))", 1, noon.timestamp()
            )

            stored = await pool.fetchrow("SELECT started_at FROM request_logs WHERE id = $1", 1)
            assert stored is not None
            assert stored["started_at"] == "2026-08-10T12:00:00"

            # after=midnight the same day: the noon row is >= midnight, must match.
            midnight = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)
            rows = await pool.fetch("SELECT id FROM request_logs WHERE started_at >= $1", midnight)
            assert [row["id"] for row in rows] == [1]

            # before=midnight the *next* day: the noon row is < next midnight, must match.
            next_midnight = datetime(2026, 8, 11, 0, 0, 0, tzinfo=timezone.utc)
            rows = await pool.fetch("SELECT id FROM request_logs WHERE started_at < $1", next_midnight)
            assert [row["id"] for row in rows] == [1]
        finally:
            await pool.close()


class TestNormalizeLegacyDatetimeBindsMigration:
    """Regression test for migrations/sqlite/024_normalize_legacy_datetime_binds.sql.

    The `_convert_arg` fix above only changes how *new* `datetime` binds are
    serialized. A row written before the fix landed still has stdlib
    sqlite3's legacy isoformat(" ") (space-separated) value in the database.
    Without a backfill, a legacy same-day row always compares as "earlier"
    than a same-day cutoff bound in the new "T" form -- regardless of actual
    time-of-day, because ' ' (0x20) sorts before 'T' (0x54) at the
    date/time separator. That silently makes retention purge/archive
    (retention/purger.py, retention/archiver.py) sweep up legacy rows before
    their real retention window elapses. Migration 024 fixes this in place
    (also covering request_logs.started_at/completed_at, written via
    to_timestamp(?) rather than a raw `datetime` bind -- see
    TestSqlitePool.test_to_timestamp_write_comparable_with_datetime_bind_filter
    for the matching write-path fix in `_translate_params`).
    """

    _MIGRATION_024 = (
        Path(__file__).resolve().parents[4] / "migrations" / "sqlite" / "024_normalize_legacy_datetime_binds.sql"
    ).read_text()

    @staticmethod
    async def _create_migration_024_tables(pool) -> None:
        """Create the tables migration 024's UPDATEs reference (empty schema is enough)."""
        await pool.execute("CREATE TABLE conversation_calls (call_id TEXT PRIMARY KEY, created_at TEXT)")
        await pool.execute("CREATE TABLE conversation_events (id INTEGER PRIMARY KEY, created_at TEXT)")
        await pool.execute(
            "CREATE TABLE session_summaries (session_id TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT)"
        )
        await pool.execute("CREATE TABLE request_logs (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT)")

    @pytest.mark.asyncio
    async def test_legacy_row_wrongly_precedes_new_format_cutoff_until_migrated(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await self._create_migration_024_tables(pool)
            # A legacy row from 23:00 UTC, stored via stdlib sqlite3's
            # pre-#806 legacy adapter (space separator) -- chronologically
            # *after* the midnight cutoff below, so it must NOT be purged.
            await pool.execute(
                "INSERT INTO conversation_calls (call_id, created_at) VALUES ($1, $2)",
                "legacy-call",
                "2026-08-10 23:00:00+00:00",
            )
            # A retention cutoff computed post-#806: midnight the same day,
            # bound as a raw `datetime` through the now-fixed `_convert_arg`.
            cutoff = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)

            # RED: pre-migration, the legacy row wrongly satisfies `created_at
            # < cutoff` even though 23:00 is chronologically after midnight.
            rows = await pool.fetch("SELECT call_id FROM conversation_calls WHERE created_at < $1", cutoff)
            assert [row["call_id"] for row in rows] == ["legacy-call"], (
                "expected the pre-migration mixed-format bug to reproduce"
            )

            async with pool.acquire() as conn:
                await conn.executescript(self._MIGRATION_024)

            # GREEN: post-migration, the same row (now "T"-form) no longer
            # wrongly precedes the cutoff.
            rows = await pool.fetch("SELECT call_id FROM conversation_calls WHERE created_at < $1", cutoff)
            assert rows == []

            stored = await pool.fetchrow("SELECT created_at FROM conversation_calls WHERE call_id = $1", "legacy-call")
            assert stored is not None
            assert stored["created_at"] == "2026-08-10T23:00:00+00:00"
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_migration_is_idempotent_and_leaves_already_normalized_rows_alone(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await self._create_migration_024_tables(pool)
            await pool.execute(
                "INSERT INTO session_summaries (session_id, first_seen, last_seen) VALUES ($1, $2, $3)",
                "s1",
                "2026-08-10 09:00:00+00:00",
                "2026-08-10T12:00:00+00:00",
            )

            async with pool.acquire() as conn:
                await conn.executescript(self._MIGRATION_024)
                # Running it a second time must not error and must not
                # further alter already-"T" values.
                await conn.executescript(self._MIGRATION_024)

            row = await pool.fetchrow("SELECT first_seen, last_seen FROM session_summaries WHERE session_id = $1", "s1")
            assert row is not None
            assert row["first_seen"] == "2026-08-10T09:00:00+00:00"
            assert row["last_seen"] == "2026-08-10T12:00:00+00:00"
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_backfills_legacy_request_logs_started_and_completed_at(self):
        pool = await create_sqlite_pool("sqlite://:memory:")
        try:
            await self._create_migration_024_tables(pool)
            # request_logs never went through a raw `datetime` bind (it's
            # always to_timestamp(?), a float), so its legacy rows are
            # 100% of pre-migration rows, not just some -- still the same
            # space-separated shape the LIKE pattern matches.
            await pool.execute(
                "INSERT INTO request_logs (id, started_at, completed_at) VALUES ($1, $2, $3)",
                1,
                "2026-08-10 12:00:00",
                "2026-08-10 12:00:05",
            )

            async with pool.acquire() as conn:
                await conn.executescript(self._MIGRATION_024)

            row = await pool.fetchrow("SELECT started_at, completed_at FROM request_logs WHERE id = $1", 1)
            assert row is not None
            assert row["started_at"] == "2026-08-10T12:00:00"
            assert row["completed_at"] == "2026-08-10T12:00:05"
        finally:
            await pool.close()
