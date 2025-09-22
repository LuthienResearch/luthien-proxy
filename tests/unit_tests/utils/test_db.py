import pytest

from luthien_proxy.utils import db


class DummyConnection:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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
async def test_open_connection_uses_custom_connector():
    seen = {}

    async def fake_connect(url: str):
        seen["url"] = url
        return DummyConnection()

    conn = await db.open_connection(connect=fake_connect, url="postgresql://example")
    assert isinstance(conn, DummyConnection)
    assert seen["url"] == "postgresql://example"


@pytest.mark.asyncio
async def test_close_connection_awaits_async_close():
    conn = DummyConnection()
    await db.close_connection(conn)
    assert conn.closed is True


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
