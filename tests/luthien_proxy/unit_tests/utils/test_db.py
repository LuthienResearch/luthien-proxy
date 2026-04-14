import pytest

from luthien_proxy.utils import db


class DummyAcquireContext:
    def __init__(self, conn: str) -> None:
        self._conn = conn
        self.entered = False

    async def __aenter__(self) -> str:
        self.entered = True
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.entered = False


class DummyPool:
    def __init__(self, name: str = "pool") -> None:
        self.name = name
        self.closed = False
        self.acquire_calls = 0

    def acquire(self) -> DummyAcquireContext:
        self.acquire_calls += 1
        return DummyAcquireContext(f"{self.name}-conn-{self.acquire_calls}")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_create_pool_uses_custom_factory():
    seen = {}

    async def fake_factory(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return "fake-pool"

    pool = await db.create_pool(factory=fake_factory, url="postgresql://example", min_size=1, max_size=2)
    assert pool == "fake-pool"
    assert seen["url"] == "postgresql://example"
    assert seen["kwargs"] == {"min_size": 1, "max_size": 2}


@pytest.mark.asyncio
async def test_database_pool_initializes_once():
    seen = []
    dummy = DummyPool()

    async def fake_factory(url: str, **kwargs):
        seen.append(url)
        return dummy

    pool = db.DatabasePool(url="postgresql://example", factory=fake_factory)
    first = await pool.get_pool()
    second = await pool.get_pool()

    assert first is dummy
    assert second is dummy
    assert seen == ["postgresql://example"]


@pytest.mark.asyncio
async def test_database_pool_connection_context():
    dummy = DummyPool()

    async def fake_factory(url: str, **kwargs):
        return dummy

    pool = db.DatabasePool(url="postgresql://example", factory=fake_factory)

    async with pool.connection() as conn:
        assert conn == "pool-conn-1"

    assert dummy.acquire_calls == 1


@pytest.mark.asyncio
async def test_database_pool_in_memory_sqlite_fixture_pattern():
    """Canonical pattern for tests that need a DatabasePool with pre-populated schema.

    This exists as the documented replacement for the `DatabasePool.__new__(...)
    + private-attribute poke` hack. Tests that previously constructed a
    `SqlitePool` directly and wrapped it should use this pattern instead:

      1. Construct a DatabasePool via its public constructor.
      2. Prime it once via `get_pool()` and do schema/fixture setup on the
         returned pool.
      3. Pass the DatabasePool anywhere a real one is expected — `get_pool()`
         returns the same cached instance, so schema set up in step 2 is
         visible throughout.

    If this test ever breaks because `DatabasePool.__init__` grows a new
    required field, that's the signal for downstream tests: they need to
    update along with it — there is no hidden bypass.
    """
    pool = db.DatabasePool("sqlite://:memory:")
    try:
        backing_pool = await pool.get_pool()
        await backing_pool.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
        await backing_pool.execute("INSERT INTO widgets (id, name) VALUES ($1, $2)", 1, "sprocket")

        # Subsequent get_pool() calls return the same cached instance so the
        # schema and rows seeded above are visible.
        again = await pool.get_pool()
        assert again is backing_pool

        # Reading through the public `connection()` context manager sees the
        # seeded row, proving downstream consumers don't need any back-door
        # access to the underlying SqlitePool.
        async with pool.connection() as conn:
            row = await conn.fetchrow("SELECT name FROM widgets WHERE id = $1", 1)
        assert row is not None
        assert row["name"] == "sprocket"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_database_pool_close_resets():
    pools = [DummyPool(name="first"), DummyPool(name="second")]

    async def fake_factory(url: str, **kwargs):
        return pools.pop(0)

    pool = db.DatabasePool(url="postgresql://example", factory=fake_factory)

    first = await pool.get_pool()
    await pool.close()
    second = await pool.get_pool()

    assert first.closed is True
    assert second.name == "second"
