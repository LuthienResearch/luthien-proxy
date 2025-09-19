import pytest

from luthien_proxy.utils import db


class DummyConnection:
    def __init__(self) -> None:
        self.closed = False

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
