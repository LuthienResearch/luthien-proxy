import pytest

from luthien_proxy.utils import redis_client


class FakeRedis:
    def __init__(self) -> None:
        self.closed = False
        self.pings = 0

    async def ping(self) -> None:
        self.pings += 1

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_get_client_caches_instance():
    fake = FakeRedis()
    seen = []

    def fake_from_url(url: str):
        seen.append(url)
        return fake

    manager = redis_client.RedisClientManager(factory=fake_from_url)

    client = await manager.get_client("redis://localhost:6379/0")
    assert client is fake
    assert fake.pings == 1

    again = await manager.get_client("redis://localhost:6379/0")
    assert again is fake
    assert fake.pings == 1
    assert seen == ["redis://localhost:6379/0"]


@pytest.mark.asyncio
async def test_close_client_evicts_cache():
    created: list[FakeRedis] = []

    def factory(url: str) -> FakeRedis:
        fake = FakeRedis()
        created.append(fake)
        return fake

    manager = redis_client.RedisClientManager(factory=factory)

    first = await manager.get_client("redis://localhost:6379/0")
    await manager.close_client("redis://localhost:6379/0")

    assert created[0] is first
    assert created[0].closed is True

    second = await manager.get_client("redis://localhost:6379/0")
    assert created[1] is second
    assert len(created) == 2
