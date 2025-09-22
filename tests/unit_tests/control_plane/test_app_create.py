from collections import Counter

import pytest

import luthien_proxy.control_plane.app as app_mod
from luthien_proxy.utils.project_config import ProjectConfig


class _DummyConn:
    def __init__(self):
        self.executed = []

    async def execute(self, query, debug_type, payload_json):
        self.executed.append((query, debug_type, payload_json))


_created_pools = []


class _DummyPool:
    def __init__(self, url):
        self.url = url
        self.get_pool_called = False
        self.closed = False
        self.conn = _DummyConn()
        _created_pools.append(self)

    async def get_pool(self):
        self.get_pool_called = True

    def connection(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    async def close(self):
        self.closed = True


class _DummyRedisClient:  # pragma: no cover - simple sentinel
    pass


_managers = []


class _DummyRedisManager:
    def __init__(self):
        self.requests = []
        self.closed = False
        _managers.append(self)

    async def get_client(self, url: str):
        self.requests.append(url)
        return _DummyRedisClient()

    async def close_all(self):
        self.closed = True


@pytest.mark.asyncio
async def test_create_control_plane_app_initializes_state(monkeypatch):
    monkeypatch.setattr(app_mod.db, "DatabasePool", _DummyPool)
    monkeypatch.setattr(app_mod.redis_client, "RedisClientManager", _DummyRedisManager)

    policy_instance = object()
    monkeypatch.setattr(app_mod, "load_policy_from_config", lambda *args, **kwargs: policy_instance)

    env = {
        "LITELLM_CONFIG_PATH": "config.yaml",
        "REDIS_URL": "redis://localhost:6379/0",
        "LUTHIEN_POLICY_CONFIG": "policy.yaml",
        "DATABASE_URL": "postgres://user:pass@localhost:5432/db",
    }
    config = ProjectConfig(env_map=env)

    app = app_mod.create_control_plane_app(config)

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.hook_counters, Counter)
        dummy_pool = _created_pools[-1]
        assert dummy_pool.get_pool_called
        assert app.state.project_config is config
        assert app.state.database_pool is dummy_pool
        assert app.state.active_policy is policy_instance
        assert _managers[-1].requests == [env["REDIS_URL"]]
        assert app.state.stream_store is not None

        await app.state.debug_log_writer("hook:test", {"payload": {"value": 1}})
        assert dummy_pool.conn.executed

    dummy_pool = _created_pools[-1]
    manager = _managers[-1]
    assert dummy_pool.closed
    assert manager.closed
    assert app.state.database_pool is None
    assert app.state.active_policy is None
    assert app.state.stream_store is None
