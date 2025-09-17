from typing import Dict

import pytest

from luthien_control.control_plane.stream_context import StreamContextStore


class FakeAsyncRedis:
    def __init__(self) -> None:
        # store string keys to bytes values; separate index keys
        self._store: Dict[str, bytes] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def append(self, key: str, value: str):
        current = self._store.get(key, b"")
        self._store[key] = current + value.encode()
        return len(self._store[key])

    async def incr(self, key: str):
        raw = self._store.get(key)
        try:
            cur = int(raw.decode()) if isinstance(raw, (bytes, bytearray)) else int(raw)  # type: ignore[arg-type]
        except Exception:
            cur = 0
        cur += 1
        self._store[key] = str(cur).encode()
        return cur

    async def expire(self, key: str, ttl: int):
        # No-op for fake client
        return True

    async def delete(self, key: str):
        self._store.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_stream_context_store_accumulate_and_clear():
    redis = FakeAsyncRedis()
    store = StreamContextStore(redis_client=redis, ttl_seconds=60)

    call_id = "abc123"
    assert await store.get_accumulated(call_id) == ""
    assert await store.get_index(call_id) == 0

    await store.append_delta(call_id, "Hello")
    await store.append_delta(call_id, ", world")

    assert await store.get_accumulated(call_id) == "Hello, world"
    assert await store.get_index(call_id) == 2

    await store.clear(call_id)
    assert await store.get_accumulated(call_id) == ""
    assert await store.get_index(call_id) == 0
