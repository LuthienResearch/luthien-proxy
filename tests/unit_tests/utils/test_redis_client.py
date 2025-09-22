import types

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
async def test_get_client_caches_instance(monkeypatch):
    redis_client._reset_cache()
    fake = FakeRedis()
    seen = []

    def fake_from_url(url: str):
        seen.append(url)
        return fake

    monkeypatch.setattr(redis_client, "redis", types.SimpleNamespace(from_url=fake_from_url))

    client = await redis_client.get_client("redis://localhost:6379/0")
    assert client is fake
    assert fake.pings == 1

    again = await redis_client.get_client("redis://localhost:6379/0")
    assert again is fake
    assert fake.pings == 1
    assert seen == ["redis://localhost:6379/0"]


@pytest.mark.asyncio
async def test_close_client_evicts_cache(monkeypatch):
    redis_client._reset_cache()
    fake = FakeRedis()

    def fake_from_url(url: str):
        return fake

    monkeypatch.setattr(redis_client, "redis", types.SimpleNamespace(from_url=fake_from_url))

    await redis_client.get_client("redis://localhost:6379/0")
    await redis_client.close_client("redis://localhost:6379/0")

    assert fake.closed is True

    # fetching again should recreate (using the patched factory)
    seen = []

    def another_from_url(url: str):
        seen.append(url)
        return FakeRedis()

    monkeypatch.setattr(redis_client, "redis", types.SimpleNamespace(from_url=another_from_url))
    await redis_client.get_client("redis://localhost:6379/0")
    assert seen == ["redis://localhost:6379/0"]
