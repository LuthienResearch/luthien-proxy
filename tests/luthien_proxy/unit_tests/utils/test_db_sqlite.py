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

    def test_dollar_n_in_line_comment_is_substituted(self):
        # Documenting current behavior: `--` line comments are NOT parsed, so a
        # $N inside a comment gets rewritten. This is usually harmless (the
        # comment just gets a `?` in it) but is surprising; if this bites,
        # revisit and add comment-stripping.
        query = "SELECT $1 -- see $2 below\nFROM t"
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
