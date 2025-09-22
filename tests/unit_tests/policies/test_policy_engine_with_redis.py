import json

import pytest

from luthien_proxy.policies.engine import PolicyEngine


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.lists: dict[str, list[str]] = {}

    async def ping(self):
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.data[key] = value.encode() if isinstance(value, str) else value
        return True

    async def lpush(self, key: str, value: str):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])


@pytest.mark.asyncio
async def test_engine_redis_state_and_logging():
    eng = PolicyEngine(database_url=None, redis_url="redis://localhost:6379/0")
    fake = FakeAsyncRedis()
    eng.redis_client = fake  # inject

    # Episode state initializes and persists
    state = await eng.get_episode_state("cid")
    assert state.get("episode_id") == "cid"
    assert json.loads(fake.data.get("episode:cid").decode()).get("episode_id") == "cid"  # type: ignore[union-attr]

    await eng.update_episode_state("cid", {"step_count": 2})
    new_state = json.loads(fake.data.get("episode:cid").decode())  # type: ignore[union-attr]
    assert new_state.get("step_count") == 2

    # Log decision writes a Redis record
    await eng.log_decision("ep1", "s1", "test", 0.2, 0.5, {"a": 1})
    dkey = "decision:ep1:s1"
    assert dkey in fake.data

    # Trigger audit pushes onto a Redis list
    await eng.trigger_audit("ep1", "s1", "why", 0.9, {"k": True})
    assert fake.lists.get("audit_queue")
