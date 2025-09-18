import pytest

from luthien_proxy.control_plane.stream_context import StreamContextStore


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.kv: dict[str, bytes | str] = {}

    async def get(self, key: str):
        return self.kv.get(key)

    async def append(self, key: str, value: str):
        cur = self.kv.get(key, b"")
        if isinstance(cur, (bytes, bytearray)):
            cur = cur.decode()
        self.kv[key] = (cur or "") + value
        return len(self.kv[key])

    async def incr(self, key: str):
        v = self.kv.get(key, b"0")
        try:
            cur = int(v.decode() if isinstance(v, (bytes, bytearray)) else v)
        except Exception:
            cur = 0
        cur += 1
        self.kv[key] = str(cur).encode()
        return cur

    async def expire(self, key: str, ttl: int):
        return True

    async def delete(self, key: str):
        self.kv.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_edges_for_empty_and_bad_values():
    r = FakeAsyncRedis()
    store = StreamContextStore(redis_client=r, ttl_seconds=10)

    # Empty call_id returns defaults
    assert await store.get_accumulated(None) == ""
    assert await store.get_index("") == 0

    # Non-bytes value in text
    r.kv["stream:x:text"] = "abc"
    assert await store.get_accumulated("x") == "abc"

    # Bad integer in index
    r.kv["stream:x:index"] = b"not-an-int"
    assert await store.get_index("x") == 0

    # append_delta short-circuits on empty text
    await store.append_delta("x", "")
    assert await store.get_index("x") == 0
