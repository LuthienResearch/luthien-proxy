"""Unit tests for PolicyCache — DB-backed policy caching."""

from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.db_sqlite import SqlitePool
from luthien_proxy.utils.policy_cache import DEFAULT_MAX_ENTRIES, PolicyCache


@pytest.fixture
async def db_pool():
    """Create an in-memory SQLite pool with the policy_cache table."""
    pool = SqlitePool(":memory:")
    # Create the table manually (normally done by migrations)
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS policy_cache ("
        "policy_name TEXT NOT NULL, "
        "cache_key TEXT NOT NULL, "
        "value_json TEXT NOT NULL, "
        "expires_at TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (policy_name, cache_key))"
    )
    yield pool
    await pool.close()


def _wrap_sqlite_pool(pool: SqlitePool) -> DatabasePool:
    """Wrap a SqlitePool in a DatabasePool for testing."""
    db = DatabasePool.__new__(DatabasePool)
    db._url = "sqlite:///:memory:"
    db._is_sqlite = True
    db._sqlite_pool = pool
    db._pool = None
    db._lock = None
    db._factory = None
    db._pool_kwargs = {}
    return db


@pytest.fixture
def cache(db_pool: SqlitePool):
    """Create a PolicyCache wrapping the in-memory pool."""
    return PolicyCache(_wrap_sqlite_pool(db_pool), "test_policy")


class TestPolicyCacheGetPut:
    @pytest.mark.asyncio
    async def test_put_and_get(self, cache: PolicyCache):
        await cache.put("key1", {"foo": "bar"}, ttl_seconds=3600)
        result = await cache.get("key1")
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_get_miss(self, cache: PolicyCache):
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, cache: PolicyCache):
        await cache.put("key1", {"v": 1}, ttl_seconds=3600)
        await cache.put("key1", {"v": 2}, ttl_seconds=3600)
        result = await cache.get("key1")
        assert result == {"v": 2}

    @pytest.mark.asyncio
    async def test_expired_entry_returns_none(self, cache: PolicyCache):
        """Entry with negative TTL is immediately expired."""
        await cache.put("expired", {"data": True}, ttl_seconds=-1)
        result = await cache.get("expired")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, cache: PolicyCache):
        await cache.put("key1", {"v": 1}, ttl_seconds=3600)
        await cache.delete("key1")
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self, cache: PolicyCache):
        await cache.delete("nope")  # should not raise


class TestPolicyCacheJsonValues:
    @pytest.mark.asyncio
    async def test_put_and_get_list(self, cache: PolicyCache):
        """Cache accepts lists (not just dicts) and round-trips them."""
        await cache.put("items", [1, 2, {"nested": True}], ttl_seconds=3600)
        result = await cache.get("items")
        assert result == [1, 2, {"nested": True}]


class TestPolicyCacheJsonbDecoding:
    """Regression tests for asyncpg JSONB returning already-decoded values.

    asyncpg may hand back JSONB columns as dict/list depending on connection
    config. SQLite always returns str. The get() path must handle all three.
    """

    def _mock_cache_with_row_value(self, raw_value: object) -> PolicyCache:
        """Build a PolicyCache whose pool.fetchrow returns {'value_json': raw_value}."""
        fake_pool = MagicMock()
        fake_pool.fetchrow = AsyncMock(return_value={"value_json": raw_value})

        db = MagicMock(spec=DatabasePool)
        db.get_pool = AsyncMock(return_value=fake_pool)
        db.is_sqlite = False  # pretend Postgres so NOW() is used
        return PolicyCache(db, "test_policy")

    @pytest.mark.asyncio
    async def test_get_returns_dict_directly_when_asyncpg_decodes(self):
        """If asyncpg already decoded JSONB to a dict, return it as-is (no json.loads)."""
        expected = {"foo": "bar", "nested": {"k": 1}}
        cache = self._mock_cache_with_row_value(expected)
        result = await cache.get("any_key")
        assert result == expected
        assert result is expected  # identity: not re-parsed

    @pytest.mark.asyncio
    async def test_get_returns_list_directly_when_asyncpg_decodes(self):
        """Top-level JSON arrays are legal; if pre-decoded, return as-is."""
        expected = [1, 2, {"nested": True}]
        cache = self._mock_cache_with_row_value(expected)
        result = await cache.get("any_key")
        assert result == expected
        assert result is expected

    @pytest.mark.asyncio
    async def test_get_parses_str_payload(self):
        """When the backend hands back a str (SQLite or asyncpg without codec), parse it."""
        cache = self._mock_cache_with_row_value('{"foo": "bar"}')
        result = await cache.get("any_key")
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_get_raises_on_unexpected_type(self):
        """Anything other than str/dict/list is a driver bug — fail loud."""
        cache = self._mock_cache_with_row_value(42)
        with pytest.raises(TypeError, match="unexpected value_json type"):
            await cache.get("any_key")


class TestPolicyCacheRoundTrip:
    """Round-trip coverage for non-trivial values.

    These tests would catch type-codec, encoding, or JSON-serialization
    regressions in either backend (SQLite TEXT or Postgres jsonb). The
    base tests above only exercise toy dicts like ``{foo: bar}``.
    """

    @pytest.mark.asyncio
    async def test_deeply_nested_dict(self, cache: PolicyCache):
        value = {
            "level1": {
                "level2": {
                    "level3": {
                        "level4": {
                            "level5": {"leaf": "deep_value", "count": 42},
                        },
                    },
                },
            },
        }
        await cache.put("nested", value, ttl_seconds=3600)
        assert await cache.get("nested") == value

    @pytest.mark.asyncio
    async def test_mixed_nested_dict_and_list(self, cache: PolicyCache):
        value = {
            "users": [
                {"id": 1, "tags": ["admin", "owner"], "meta": {"active": True}},
                {"id": 2, "tags": [], "meta": {"active": False, "reason": None}},
            ],
            "counts": {"total": 2, "active": 1},
            "matrix": [[1, 2, 3], [4, 5, 6]],
        }
        await cache.put("mixed", value, ttl_seconds=3600)
        assert await cache.get("mixed") == value

    @pytest.mark.asyncio
    async def test_unicode_basic_multilingual(self, cache: PolicyCache):
        # WHY: json.dumps defaults to ensure_ascii=True, so the stored
        # TEXT is actually pure ASCII escape sequences (\uXXXX). The
        # contract we care about is that the Python object round-trips
        # identically — this verifies the full encode → store → decode
        # → reconstruct path without mojibake on either backend.
        value = {
            "greek": "αβγδε",
            "cyrillic": "Привет мир",
            "chinese": "你好世界",
            "japanese": "こんにちは",
            "korean": "안녕하세요",
            "arabic": "مرحبا بالعالم",
            "hebrew": "שלום עולם",
        }
        await cache.put("unicode_bmp", value, ttl_seconds=3600)
        assert await cache.get("unicode_bmp") == value

    @pytest.mark.asyncio
    async def test_unicode_supplementary_plane_and_emoji(self, cache: PolicyCache):
        # Emoji and supplementary plane codepoints (> U+FFFF) — exercise
        # surrogate-pair handling in any UTF-16 intermediate layers.
        value = {
            "emoji_simple": "🙂🔥🚀",
            "emoji_flags": "🇺🇸🇯🇵🇫🇷",
            "emoji_zwj": "👨‍👩‍👧‍👦",  # family via ZWJ sequence
            "emoji_skin_tone": "👋🏽👍🏿",
            "math": "𝔸𝕀𝕄",  # mathematical alphanumeric symbols
            "cuneiform": "𒀀𒀁𒀂",
        }
        await cache.put("unicode_smp", value, ttl_seconds=3600)
        assert await cache.get("unicode_smp") == value

    @pytest.mark.asyncio
    async def test_unicode_combining_and_normalization(self, cache: PolicyCache):
        # NFC vs NFD forms must round-trip byte-for-byte — JSON storage
        # must not silently normalize.
        nfc = "café"  # precomposed é (U+00E9)
        nfd = "cafe\u0301"  # e + combining acute
        value = {"nfc": nfc, "nfd": nfd}
        await cache.put("normalization", value, ttl_seconds=3600)
        result = await cache.get("normalization")
        assert result == value
        assert result["nfc"] != result["nfd"], "NFC and NFD must stay distinct"
        assert len(result["nfc"]) == 4
        assert len(result["nfd"]) == 5

    @pytest.mark.asyncio
    async def test_string_with_json_control_chars(self, cache: PolicyCache):
        # Characters that need escaping in JSON — quotes, backslashes,
        # newlines, tabs, control chars.
        value = {
            "quote": 'he said "hello"',
            "backslash": "path\\to\\file",
            "newline": "line1\nline2\r\nline3",
            "tab": "col1\tcol2",
            "null_byte_ish": "before\u0000after",  # NUL
            "bell": "\a\b\f",
            "json_lookalike": '{"not": "actually parsed"}',
            "mixed": 'say "hi"\nand\t"bye"\\',
        }
        await cache.put("control_chars", value, ttl_seconds=3600)
        assert await cache.get("control_chars") == value

    @pytest.mark.asyncio
    async def test_large_payload_100kb(self, cache: PolicyCache):
        # Exercise storage of ~100KB payloads — would catch TEXT/BLOB
        # column size truncation or buffer-size assumptions.
        chunk = "a" * 1024
        value = {"data": [chunk] * 100, "tag": "large"}
        await cache.put("large", value, ttl_seconds=3600)
        result = await cache.get("large")
        assert result == value
        assert len(result["data"]) == 100
        assert all(len(s) == 1024 for s in result["data"])

    @pytest.mark.asyncio
    async def test_large_unicode_payload(self, cache: PolicyCache):
        # Large payload with multi-byte characters — bytes != chars.
        chunk = "日本語テスト" * 100  # ~600 chars, ~1800 UTF-8 bytes per chunk
        value = {"text": chunk * 50}
        await cache.put("large_unicode", value, ttl_seconds=3600)
        result = await cache.get("large_unicode")
        assert result == value
        assert result["text"] == chunk * 50

    @pytest.mark.asyncio
    async def test_scalar_top_level_string(self, cache: PolicyCache):
        await cache.put("s", "just a string", ttl_seconds=3600)
        result = await cache.get("s")
        assert result == "just a string"
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_scalar_top_level_int(self, cache: PolicyCache):
        await cache.put("i", 42, ttl_seconds=3600)
        result = await cache.get("i")
        assert result == 42
        assert isinstance(result, int)
        assert not isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_scalar_top_level_float(self, cache: PolicyCache):
        await cache.put("f", 3.14159, ttl_seconds=3600)
        result = await cache.get("f")
        assert result == 3.14159
        assert isinstance(result, float)

    @pytest.mark.asyncio
    async def test_scalar_top_level_bool(self, cache: PolicyCache):
        await cache.put("t", True, ttl_seconds=3600)
        await cache.put("f", False, ttl_seconds=3600)
        assert await cache.get("t") is True
        assert await cache.get("f") is False

    @pytest.mark.asyncio
    async def test_bool_not_coerced_to_int(self, cache: PolicyCache):
        # JSON preserves bool vs int distinction — a True stored should
        # not come back as 1. Important because Python bools are an
        # int subclass and naive storage layers sometimes lose the type.
        await cache.put("b", True, ttl_seconds=3600)
        result = await cache.get("b")
        assert result is True
        assert type(result) is bool

        await cache.put("i", 1, ttl_seconds=3600)
        result_int = await cache.get("i")
        assert result_int == 1
        assert type(result_int) is int

    @pytest.mark.asyncio
    async def test_none_value_round_trip(self, cache: PolicyCache):
        # None is a valid JSON value (`null`). The cache stores it but
        # get() returns None on both a miss and a stored-None, so this
        # test documents that behavior: stored None is retrievable but
        # indistinguishable from a miss. If a future PR changes get()
        # to raise or sentinel-distinguish, this test will flag it.
        await cache.put("nil", None, ttl_seconds=3600)
        assert await cache.get("nil") is None
        assert await cache.get("not_stored") is None

    @pytest.mark.asyncio
    async def test_integer_edge_values(self, cache: PolicyCache):
        # Large integers that exceed 32-bit and 64-bit signed range.
        # JSON has no int size limit but some numeric codecs coerce to
        # float once the mantissa overflows.
        value = {
            "zero": 0,
            "negative": -1,
            "max_i32": 2**31 - 1,
            "min_i32": -(2**31),
            "max_i64": 2**63 - 1,
            "min_i64": -(2**63),
            "beyond_i64": 2**80,  # Python int, would overflow JS Number
        }
        await cache.put("ints", value, ttl_seconds=3600)
        result = await cache.get("ints")
        assert result == value
        for k, v in value.items():
            assert type(result[k]) is int, f"{k} should stay int, got {type(result[k])}"

    @pytest.mark.asyncio
    async def test_float_edge_values(self, cache: PolicyCache):
        # JSON doesn't support NaN/Infinity, so we exclude those — but
        # negative zero, tiny subnormals, and very large finite floats
        # should round-trip.
        value = {
            "zero": 0.0,
            "negative": -1.5,
            "small": 1e-300,
            "large": 1e300,
            "pi": 3.141592653589793,
        }
        await cache.put("floats", value, ttl_seconds=3600)
        result = await cache.get("floats")
        assert result == value
        assert math.isclose(result["pi"], math.pi)

    @pytest.mark.asyncio
    async def test_non_finite_floats_current_behavior(self, cache: PolicyCache):
        # json.dumps with default settings accepts NaN/Infinity (emitting
        # the non-standard `NaN`/`Infinity` tokens). This test pins the
        # current round-trip behavior so any future switch to
        # json.dumps(..., allow_nan=False) is a visible change: the test
        # will start raising ValueError inside put(), which is what
        # callers would then need to handle.
        await cache.put("nan", float("nan"), ttl_seconds=3600)
        nan_result = await cache.get("nan")
        assert isinstance(nan_result, float)
        assert math.isnan(nan_result)

        await cache.put("inf", float("inf"), ttl_seconds=3600)
        assert await cache.get("inf") == float("inf")

        await cache.put("ninf", float("-inf"), ttl_seconds=3600)
        assert await cache.get("ninf") == float("-inf")

    @pytest.mark.asyncio
    async def test_empty_containers(self, cache: PolicyCache):
        await cache.put("empty_dict", {}, ttl_seconds=3600)
        await cache.put("empty_list", [], ttl_seconds=3600)
        await cache.put("empty_string", "", ttl_seconds=3600)

        assert await cache.get("empty_dict") == {}
        assert await cache.get("empty_list") == []
        assert await cache.get("empty_string") == ""

    @pytest.mark.asyncio
    async def test_heterogeneous_list(self, cache: PolicyCache):
        value = [1, "two", 3.0, True, False, None, {"k": "v"}, [1, 2], []]
        await cache.put("hetero", value, ttl_seconds=3600)
        result = await cache.get("hetero")
        assert result == value
        assert type(result[0]) is int
        assert type(result[2]) is float
        assert result[3] is True
        assert result[4] is False
        assert result[5] is None

    @pytest.mark.asyncio
    async def test_overwrite_with_different_type(self, cache: PolicyCache):
        # Upserting should fully replace the value, including its type.
        await cache.put("k", {"was": "dict"}, ttl_seconds=3600)
        await cache.put("k", [1, 2, 3], ttl_seconds=3600)
        result = await cache.get("k")
        assert result == [1, 2, 3]
        assert isinstance(result, list)

        await cache.put("k", "now a string", ttl_seconds=3600)
        assert await cache.get("k") == "now a string"

        await cache.put("k", 99, ttl_seconds=3600)
        assert await cache.get("k") == 99

    @pytest.mark.asyncio
    async def test_dict_with_unicode_keys(self, cache: PolicyCache):
        value = {"café": 1, "你好": 2, "🔑": "key", "مفتاح": "arabic key"}
        await cache.put("unicode_keys", value, ttl_seconds=3600)
        assert await cache.get("unicode_keys") == value

    @pytest.mark.asyncio
    async def test_cache_key_with_unicode_and_special_chars(self, cache: PolicyCache):
        # The cache_key itself (SQL param, not JSON) — should survive
        # arbitrary unicode and SQL-meta characters.
        keys = [
            "key with spaces",
            "key/with/slashes",
            "key:with:colons",
            "key'with'quotes",
            'key"with"doublequotes',
            "key;DROP TABLE policy_cache;--",
            "键_unicode",
            "🔑_emoji_key",
            "a" * 500,  # long key
        ]
        for i, key in enumerate(keys):
            await cache.put(key, {"idx": i, "key": key}, ttl_seconds=3600)
        for i, key in enumerate(keys):
            result = await cache.get(key)
            assert result == {"idx": i, "key": key}, f"failed for key {key!r}"

    @pytest.mark.asyncio
    async def test_empty_string_cache_key(self, cache: PolicyCache):
        # Empty string is a legal SQL parameter value. The cache should
        # treat it as a normal key, not conflate it with "no key".
        await cache.put("", {"marker": "empty_key"}, ttl_seconds=3600)
        assert await cache.get("") == {"marker": "empty_key"}
        # And it must not collide with other keys.
        await cache.put("other", {"marker": "other"}, ttl_seconds=3600)
        assert await cache.get("") == {"marker": "empty_key"}

    @pytest.mark.asyncio
    async def test_ttl_zero_is_immediately_expired(self, cache: PolicyCache):
        # policy_cache uses strict > in the expires_at comparison, so
        # ttl=0 stores an entry whose expires_at equals NOW() and is
        # therefore already expired on read. This test pins that
        # behavior — a future change to >= would flip it.
        await cache.put("zero_ttl", {"v": 1}, ttl_seconds=0)
        assert await cache.get("zero_ttl") is None

    @pytest.mark.asyncio
    async def test_dict_with_non_string_keys_coerces_to_string(self, cache: PolicyCache):
        # JSON only supports string keys — json.dumps silently coerces
        # int/float/bool keys to strings. This documents the behavior
        # so callers who assume dict keys round-trip unchanged get a
        # heads-up from the test suite.
        await cache.put("coerced", {1: "one", 2: "two"}, ttl_seconds=3600)
        result = await cache.get("coerced")
        assert result == {"1": "one", "2": "two"}
        assert all(isinstance(k, str) for k in result)

    @pytest.mark.asyncio
    async def test_nested_bool_and_none_in_lists(self, cache: PolicyCache):
        # Top-level heterogeneous lists are covered above, but nested
        # positions are a separate code path in some JSON codecs.
        value = [[True, False], [None, None], [[True, None, False]]]
        await cache.put("nested_typed", value, ttl_seconds=3600)
        result = await cache.get("nested_typed")
        assert result == value
        assert result[0][0] is True
        assert result[0][1] is False
        assert result[1][0] is None
        assert result[2][0][0] is True
        assert result[2][0][1] is None
        assert result[2][0][2] is False

    @pytest.mark.asyncio
    async def test_policy_name_isolation_with_unicode(self, db_pool: SqlitePool):
        # Namespacing must hold even when policy names share a key and
        # contain non-ASCII characters.
        db = _wrap_sqlite_pool(db_pool)
        cache_a = PolicyCache(db, "policy_αβγ")
        cache_b = PolicyCache(db, "policy_xyz")
        await cache_a.put("k", {"ns": "a"}, ttl_seconds=3600)
        await cache_b.put("k", {"ns": "b"}, ttl_seconds=3600)
        assert await cache_a.get("k") == {"ns": "a"}
        assert await cache_b.get("k") == {"ns": "b"}

    @pytest.mark.asyncio
    async def test_concurrent_puts_leave_consistent_state(self, cache: PolicyCache):
        # The put() docstring claims atomicity via ON CONFLICT, so
        # concurrent puts on the same key must leave the row in one of
        # the two valid states (not a mix). SQLite serializes writes
        # via SqlitePool's lock, so this mainly verifies the fixture
        # doesn't corrupt under interleaved awaits — but it would
        # catch a future regression where put() splits into a
        # non-atomic SELECT+INSERT sequence.
        await asyncio.gather(
            cache.put("race", {"writer": "a", "payload": list(range(100))}, ttl_seconds=3600),
            cache.put("race", {"writer": "b", "payload": list(range(100))}, ttl_seconds=3600),
            cache.put("race", {"writer": "c", "payload": list(range(100))}, ttl_seconds=3600),
        )
        result = await cache.get("race")
        assert isinstance(result, dict)
        assert result["writer"] in {"a", "b", "c"}
        assert result["payload"] == list(range(100))


class TestPolicyCacheIsolation:
    @pytest.mark.asyncio
    async def test_different_policies_isolated(self, db_pool: SqlitePool):
        """Two PolicyCache instances with different names don't see each other's entries."""
        db = _wrap_sqlite_pool(db_pool)

        cache_a = PolicyCache(db, "policy_a")
        cache_b = PolicyCache(db, "policy_b")

        await cache_a.put("shared_key", {"from": "a"}, ttl_seconds=3600)
        await cache_b.put("shared_key", {"from": "b"}, ttl_seconds=3600)

        result_a = await cache_a.get("shared_key")
        result_b = await cache_b.get("shared_key")

        assert result_a == {"from": "a"}
        assert result_b == {"from": "b"}


class TestPolicyCacheCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_expired(self, cache: PolicyCache):
        await cache.put("expired1", {"v": 1}, ttl_seconds=-1)
        await cache.put("expired2", {"v": 2}, ttl_seconds=-1)
        await cache.put("valid", {"v": 3}, ttl_seconds=3600)

        deleted = await cache.cleanup_expired()
        assert deleted == 2

        # Valid entry should still be there
        result = await cache.get("valid")
        assert result == {"v": 3}


async def _count_entries(pool: SqlitePool, policy_name: str) -> int:
    """Count rows in the policy_cache table for a given namespace."""
    row = await pool.fetchrow(
        "SELECT COUNT(*) as n FROM policy_cache WHERE policy_name = ?",
        policy_name,
    )
    assert row is not None
    return int(row["n"])


class TestPolicyCacheSchema:
    """Schema regression tests.

    WHY: updated_at was originally added to the table but never read by any
    code path (see Trello 69d8a429). It was dropped to keep the schema minimal
    and avoid write amplification. These tests lock in that decision so a
    future refactor doesn't silently reintroduce the column (or a SQL
    statement referencing it) without an explicit consumer.
    """

    @pytest.mark.asyncio
    async def test_table_has_no_updated_at_column(self, db_pool: SqlitePool):
        """The policy_cache table must not have an updated_at column."""
        rows = await db_pool.fetch("PRAGMA table_info(policy_cache)")
        columns = {row["name"] for row in rows}
        assert "updated_at" not in columns, (
            "policy_cache.updated_at was reintroduced — if adding it back, "
            "also add a read path or stats consumer (see Trello 69d8a429)"
        )
        # Sanity: the columns we do expect are still there.
        assert {"policy_name", "cache_key", "value_json", "expires_at", "created_at"}.issubset(columns)

    @pytest.mark.asyncio
    async def test_put_sql_does_not_reference_updated_at(self, cache: PolicyCache):
        """put() must succeed against a schema without updated_at.

        This is an integration-level guard: if put()'s SQL is edited to
        reference updated_at again, this test fails because the column does
        not exist in the fixture's table.
        """
        await cache.put("probe", {"x": 1}, ttl_seconds=3600)
        result = await cache.get("probe")
        assert result == {"x": 1}


class TestPolicyCacheSizeCap:
    """Tests for the entry cap and eviction policy.

    The cap is enforced on every put(): if the post-upsert row count for this
    policy_name exceeds ``max_entries``, entries are evicted in ``ORDER BY
    created_at ASC, cache_key ASC`` order — oldest-inserted first, with
    cache_key as a deterministic tiebreak. On SQLite the default
    ``datetime('now')`` resolution is 1 second, so tests use alphabetical
    cache keys that match the intended eviction order for multi-put-in-the-
    same-second scenarios.
    """

    @pytest.mark.asyncio
    async def test_default_cap_is_reasonable(self, db_pool: SqlitePool):
        """The module-level default is large enough that existing callers aren't surprised."""
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "p")
        assert cache.max_entries == DEFAULT_MAX_ENTRIES
        assert DEFAULT_MAX_ENTRIES >= 1000  # sanity: not a tiny number

    @pytest.mark.asyncio
    async def test_rejects_non_positive_cap(self, db_pool: SqlitePool):
        """A zero or negative cap is a configuration error and must fail loudly."""
        db = _wrap_sqlite_pool(db_pool)
        with pytest.raises(ValueError, match="must be positive"):
            PolicyCache(db, "p", max_entries=0)
        with pytest.raises(ValueError, match="must be positive"):
            PolicyCache(db, "p", max_entries=-5)

    @pytest.mark.asyncio
    async def test_none_cap_means_unbounded(self, db_pool: SqlitePool):
        """max_entries=None disables enforcement — useful for migrations and tests."""
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "unbounded", max_entries=None)
        for i in range(20):
            await cache.put(f"k{i:02d}", {"v": i}, ttl_seconds=3600)

        assert await _count_entries(db_pool, "unbounded") == 20
        assert cache.max_entries is None

    @pytest.mark.asyncio
    async def test_cap_enforced_on_put(self, db_pool: SqlitePool):
        """Putting one more than the cap triggers eviction of the FIFO-oldest entry."""
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "capped", max_entries=3)

        # Alphabetical cache keys match insertion order. Under SQLite's
        # 1-second-precision created_at, the (created_at, cache_key) ORDER BY
        # falls back to the cache_key tiebreak within a single second, which
        # matches intended FIFO order for this test.
        await cache.put("k0", {"v": 0}, ttl_seconds=3600)
        await cache.put("k1", {"v": 1}, ttl_seconds=3600)
        await cache.put("k2", {"v": 2}, ttl_seconds=3600)
        assert await _count_entries(db_pool, "capped") == 3

        await cache.put("k3", {"v": 3}, ttl_seconds=3600)  # pushes over cap

        assert await _count_entries(db_pool, "capped") == 3
        assert await cache.get("k0") is None  # evicted — oldest by FIFO order
        assert await cache.get("k1") == {"v": 1}
        assert await cache.get("k2") == {"v": 2}
        assert await cache.get("k3") == {"v": 3}

    @pytest.mark.asyncio
    async def test_eviction_order_is_fifo(self, db_pool: SqlitePool):
        """Multiple evictions in one put go in FIFO order — oldest inserts first."""
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "order", max_entries=2)

        # Alphabetical order == intended FIFO order. First two survive, third
        # pushes cap. Since all three share a created_at under SQLite second
        # precision, cache_key tiebreak picks "k0" as the one to evict.
        await cache.put("k0", {"v": 0}, ttl_seconds=3600)
        await cache.put("k1", {"v": 1}, ttl_seconds=3600)
        await cache.put("k2", {"v": 2}, ttl_seconds=3600)

        assert await _count_entries(db_pool, "order") == 2
        assert await cache.get("k0") is None
        assert await cache.get("k1") == {"v": 1}
        assert await cache.get("k2") == {"v": 2}

    @pytest.mark.asyncio
    async def test_new_key_is_never_immediately_self_evicted(self, db_pool: SqlitePool):
        """A brand-new put() must not evict its own freshly-inserted row.

        This is the key semantic difference from an expires_at-based policy:
        because FIFO evicts oldest-by-created_at (and the new key has the
        largest created_at), a new entry is always safe even if its TTL is
        much shorter than the rest of the cache. Regression guard.
        """
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "selfevict", max_entries=2)

        # Fill the cache with long-lived entries using alphabetically-early
        # keys so they're the FIFO victims.
        await cache.put("a_old", {"v": "old1"}, ttl_seconds=10_000)
        await cache.put("b_old", {"v": "old2"}, ttl_seconds=10_000)

        # Put a new entry with a *shorter* TTL than the long-lived rows.
        # Under a naive expires_at-ASC eviction this would evict itself;
        # under FIFO-by-created_at the new entry survives and an old one
        # goes. TTL must be long enough to survive SQLite's second-
        # precision expires_at (datetime('now') truncates fractional
        # seconds) plus worst-case CI latency between put() and get() —
        # a 1s TTL flakes on loaded CI runners.
        await cache.put("c_new", {"v": "new"}, ttl_seconds=60)

        assert await _count_entries(db_pool, "selfevict") == 2
        assert await cache.get("c_new") == {"v": "new"}

    @pytest.mark.asyncio
    async def test_upsert_does_not_evict_the_refreshed_key(self, db_pool: SqlitePool):
        """Updating an existing key at cap does not trigger eviction of that key.

        Row count is unchanged by an upsert (ON CONFLICT update, not insert),
        so _enforce_cap sees count == cap and does nothing. Crucially the
        cap check runs *after* the upsert inside the same transaction, so
        even if the count were briefly wrong, the value must not be lost.
        """
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "updates", max_entries=2)

        await cache.put("a", {"v": 1}, ttl_seconds=3600)
        await cache.put("b", {"v": 1}, ttl_seconds=3600)
        assert await _count_entries(db_pool, "updates") == 2

        await cache.put("a", {"v": 2}, ttl_seconds=3600)  # refresh

        assert await _count_entries(db_pool, "updates") == 2
        assert await cache.get("a") == {"v": 2}
        assert await cache.get("b") == {"v": 1}

    @pytest.mark.asyncio
    async def test_cap_is_per_policy_namespace(self, db_pool: SqlitePool):
        """Caps count separately for each policy_name — siblings don't evict each other."""
        db = _wrap_sqlite_pool(db_pool)
        cache_a = PolicyCache(db, "alpha", max_entries=2)
        cache_b = PolicyCache(db, "beta", max_entries=2)

        await cache_a.put("x", {"v": 1}, ttl_seconds=3600)
        await cache_a.put("y", {"v": 2}, ttl_seconds=3600)
        await cache_b.put("x", {"v": 3}, ttl_seconds=3600)
        await cache_b.put("y", {"v": 4}, ttl_seconds=3600)

        # Each namespace is full (2/2) — neither should have evicted anything
        # because the cap is per namespace.
        assert await _count_entries(db_pool, "alpha") == 2
        assert await _count_entries(db_pool, "beta") == 2
        assert await cache_a.get("x") == {"v": 1}
        assert await cache_b.get("x") == {"v": 3}

    @pytest.mark.asyncio
    async def test_cap_does_not_evict_other_namespaces(self, db_pool: SqlitePool):
        """Filling one namespace over its cap must never delete rows from another.

        Regression guard: a buggy _enforce_cap that forgot the ``WHERE
        policy_name = $1`` filter in the SELECT would happily pick up rows
        from the oldest-inserted namespace, not the one being filled.
        """
        db = _wrap_sqlite_pool(db_pool)
        cache_a = PolicyCache(db, "alpha", max_entries=1)
        cache_b = PolicyCache(db, "beta", max_entries=1)

        # Insert beta first so beta's entry is FIFO-oldest across the whole
        # table — a cross-namespace bug would evict *it* instead of alpha's.
        await cache_b.put("beta_key", {"v": "b"}, ttl_seconds=3600)
        await cache_a.put("a1", {"v": 1}, ttl_seconds=3600)
        await cache_a.put("a2", {"v": 2}, ttl_seconds=3600)  # pushes alpha over cap

        assert await _count_entries(db_pool, "alpha") == 1
        assert await _count_entries(db_pool, "beta") == 1
        assert await cache_b.get("beta_key") == {"v": "b"}

    @pytest.mark.asyncio
    async def test_many_sequential_puts_converge_to_cap(self, db_pool: SqlitePool):
        """20 sequential puts against a cap of 5 must leave exactly 5 rows.

        SQLite serializes all writes through the shim's pool lock, so
        asyncio.gather() of 20 puts degrades to sequential execution (no
        real concurrency contention is exercised here). The test still
        verifies the transactional put+evict sequence handles long runs
        of over-cap writes without getting stuck or losing integrity.
        """
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "concurrent", max_entries=5)

        # Two-digit keys so alphabetical order matches insertion order for
        # the within-1-second created_at tiebreak.
        tasks = [cache.put(f"k{i:02d}", {"v": i}, ttl_seconds=3600) for i in range(20)]
        await asyncio.gather(*tasks)

        assert await _count_entries(db_pool, "concurrent") == 5
        # The 5 latest insertions (k15..k19) should remain; earlier ones evicted.
        for i in range(15):
            assert await cache.get(f"k{i:02d}") is None, f"k{i:02d} should have been evicted"
        for i in range(15, 20):
            assert await cache.get(f"k{i:02d}") == {"v": i}

    @pytest.mark.asyncio
    async def test_cap_counts_expired_rows_as_well(self, db_pool: SqlitePool):
        """Expired rows still occupy a slot — the cap is on row count, not live entries.

        This is intentional: ``cleanup_expired()`` is the mechanism for
        dropping dead rows by TTL. The cap is orthogonal — a hard ceiling
        on row *count*. Expired rows that happen to be the oldest will be
        evicted first by FIFO ordering, so the cap still effectively
        protects live entries in typical workloads.
        """
        cache = PolicyCache(_wrap_sqlite_pool(db_pool), "mixed", max_entries=2)

        # Alphabetical keys match intended eviction order under SQLite's
        # 1-second-precision created_at.
        await cache.put("a_dead", {"v": "d1"}, ttl_seconds=-100)
        await cache.put("b_dead", {"v": "d2"}, ttl_seconds=-50)
        await cache.put("c_live", {"v": "live"}, ttl_seconds=3600)

        assert await _count_entries(db_pool, "mixed") == 2
        # The oldest dead row is gone; the most-recent put always survives.
        assert await cache.get("c_live") == {"v": "live"}
        assert await cache.get("a_dead") is None
