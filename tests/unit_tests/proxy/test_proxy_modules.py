import types

import pytest

from luthien_proxy.proxy import __main__ as proxy_main
from luthien_proxy.proxy import start_proxy
from luthien_proxy.proxy.debug_callback import DebugCallback
from luthien_proxy.utils.project_config import ProjectConfig


def test_main_requires_config_path():
    config = ProjectConfig(env_map={})
    with pytest.raises(RuntimeError):
        proxy_main.main(config=config)


def test_litellm_command_respects_env():
    cmd = proxy_main._litellm_command(
        config_path="/cfg.yaml",
        host="127.0.0.1",
        port="4010",
        detailed_debug=True,
    )
    assert cmd[:5] == ["uv", "run", "litellm", "--config", "/cfg.yaml"]
    assert "4010" in cmd and "127.0.0.1" in cmd
    assert "--detailed_debug" in cmd


def test_proxy_main_uses_injected_runners():
    config = ProjectConfig(
        env_map={
            "LITELLM_CONFIG_PATH": "/tmp/config.yaml",
            "LITELLM_HOST": "127.0.0.1",
            "LITELLM_PORT": "4010",
            "LITELLM_DETAILED_DEBUG": "false",
        }
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_runner(cmd, **kwargs):
        calls.append((tuple(cmd), kwargs))
        return types.SimpleNamespace(returncode=0)

    proxy_main.main(prisma_runner=fake_runner, command_runner=fake_runner, config=config)
    assert calls[0][0][:4] == ("uv", "run", "prisma", "db")
    assert calls[1][0][0:3] == ("uv", "run", "litellm")


def test_start_proxy_main_runs_with_injected_dependencies():
    config = ProjectConfig(
        env_map={
            "LITELLM_CONFIG_PATH": "/tmp/config.yaml",
            "LITELLM_HOST": "127.0.0.1",
            "LITELLM_PORT": "4010",
            "LITELLM_LOG": "DEBUG",
            "CONTROL_PLANE_URL": "http://localhost:8081",
        }
    )
    litellm = types.SimpleNamespace(callbacks=[])
    fake_app = object()

    runner_calls: dict[str, object] = {}

    def fake_runner(app, host, port, log_level, reload=False):  # noqa: ARG001
        runner_calls.update({"app": app, "host": host, "port": port, "log_level": log_level})

    runtime = start_proxy.runtime_for_tests(
        config=config,
        uvicorn_runner=fake_runner,
        litellm=litellm,
        app=fake_app,
    )
    start_proxy.main(runtime)
    assert runner_calls["app"] is fake_app
    assert runner_calls["host"] == "127.0.0.1"
    assert runner_calls["port"] == 4010
    assert runner_calls["log_level"] == "debug"


class _SyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.posts: list[tuple[str, dict[str, object]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    def post(self, url, json):  # noqa: A002
        self.posts.append((url, json))


class _AsyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    async def post(self, url, json):  # noqa: A002
        self.posts.append((url, json))


class _SyncFactory:
    def __init__(self):
        self.instances: list[_SyncClient] = []

    def __call__(self, **kwargs):  # noqa: ANN001
        client = _SyncClient(**kwargs)
        self.instances.append(client)
        return client


class _AsyncFactory:
    def __init__(self):
        self.instances: list[_AsyncClient] = []

    def __call__(self, **kwargs):  # noqa: ANN001
        client = _AsyncClient(**kwargs)
        self.instances.append(client)
        return client


@pytest.mark.asyncio
async def test_debug_callback_uses_injected_clients():
    sync_factory = _SyncFactory()
    async_factory = _AsyncFactory()
    config = ProjectConfig(
        env_map={
            "LITELLM_CONFIG_PATH": "/tmp/config.yaml",
            "REDIS_URL": "redis://localhost:6379/0",
            "LUTHIEN_POLICY_CONFIG": "/tmp/policy.yaml",
        }
    )
    cb = DebugCallback(config=config, client_factory=sync_factory, async_client_factory=async_factory)

    cb.log_pre_api_call(None, None, {"k": 1})
    await cb.async_log_pre_api_call(None, None, {"k": 1})
    await cb.async_on_stream_event({}, {"a": 1}, 0, 0)
    await cb.async_post_call_success_hook({"d": {}}, None, {"choices": []})

    async def agen():
        yield {"choices": []}

    out = []
    async for item in cb.async_post_call_streaming_iterator_hook(None, agen(), {}):
        out.append(item)

    assert out == [{"choices": []}]
    assert sync_factory.instances and any(client.posts for client in sync_factory.instances)
    assert async_factory.instances and any(client.posts for client in async_factory.instances)
