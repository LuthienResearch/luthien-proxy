from collections import Counter
from typing import Any

import pytest
from fastapi.testclient import TestClient

import luthien_proxy.control_plane.app as app_mod
from luthien_proxy.policies.base import LuthienPolicy, StreamPolicyContext
from luthien_proxy.utils.project_config import ProjectConfig

pytestmark = pytest.mark.e2e


class _DummyConn:
    async def execute(self, query, debug_type, payload_json):  # pragma: no cover - smoke sentinel
        return None


class _DummyPool:
    def __init__(self, url):
        self.url = url
        self._conn = _DummyConn()

    async def get_pool(self):
        return None

    def connection(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    async def close(self):  # pragma: no cover - smoke sentinel
        return None


class _DummyRedisClient:
    def __init__(self):
        self._storage: dict[str, Any] = {}

    async def publish(self, channel: str, payload: str) -> None:  # pragma: no cover - unused in test
        self._storage[channel] = payload

    async def append(self, key: str, text: str) -> None:
        self._storage[key] = self._storage.get(key, "") + text

    async def incr(self, key: str) -> None:
        self._storage[key] = int(self._storage.get(key, 0)) + 1

    async def expire(self, key: str, ttl: int) -> None:  # pragma: no cover - no-op
        return None

    async def get(self, key: str) -> Any:
        value = self._storage.get(key)
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    async def delete(self, key: str) -> None:
        self._storage.pop(key, None)


class _DummyRedisManager:
    def __init__(self):
        self.client = _DummyRedisClient()

    async def get_client(self, url: str) -> _DummyRedisClient:
        return self.client

    async def close_all(self) -> None:  # pragma: no cover - no-op
        return None


class DummyStreamingPolicy(LuthienPolicy):
    async def generate_response_stream(
        self,
        context: StreamPolicyContext,
        incoming_stream,
    ):
        async for chunk in incoming_stream:
            context.chunk_count += 1
            transformed = {
                **chunk,
                "choices": [],
            }
            for choice in chunk.get("choices", []):
                delta = dict(choice.get("delta", {}))
                content = delta.get("content")
                if isinstance(content, str):
                    delta["content"] = f"{content} ::policy"
                transformed_choice = {**choice, "delta": delta}
                transformed.setdefault("choices", []).append(transformed_choice)
            yield transformed or chunk


def test_websocket_stream_round_trip(monkeypatch):
    monkeypatch.setattr(app_mod.db, "DatabasePool", _DummyPool)
    monkeypatch.setattr(app_mod.redis_client, "RedisClientManager", lambda: _DummyRedisManager())
    monkeypatch.setattr(app_mod, "load_policy_from_config", lambda *args, **kwargs: DummyStreamingPolicy())

    env = {
        "LITELLM_CONFIG_PATH": "config.yaml",
        "REDIS_URL": "redis://localhost:6379/0",
        "LUTHIEN_POLICY_CONFIG": "policy.yaml",
        "DATABASE_URL": "postgres://user:pass@localhost:5432/db",
    }
    config = ProjectConfig(env_map=env)

    app = app_mod.create_control_plane_app(config)

    with TestClient(app) as client:
        # sanity check the lifespan set up our dummy state
        assert isinstance(client.app.state.hook_counters, Counter)

        with client.websocket_connect("/stream/test-stream") as ws:
            ws.send_json(
                {
                    "type": "START",
                    "data": {
                        "litellm_call_id": "test-stream",
                        "model": "dummy",
                    },
                }
            )
            ws.send_json(
                {
                    "type": "CHUNK",
                    "data": {
                        "id": "chunk-1",
                        "object": "chat.completion.chunk",
                        "model": "dummy-model",
                        "created": 1,
                        "choices": [
                            {"index": 0, "delta": {"content": "hello"}},
                        ],
                    },
                }
            )

            response = ws.receive_json()
            assert response["type"] == "CHUNK"
            transformed = response["data"]["choices"][0]["delta"]["content"]
            assert transformed == "hello ::policy"

            ws.send_json({"type": "END"})
            assert ws.receive_json()["type"] == "END"
