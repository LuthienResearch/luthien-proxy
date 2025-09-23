from collections import Counter
from types import SimpleNamespace

import pytest

from luthien_proxy.control_plane.dependencies import (
    get_active_policy,
    get_database_pool,
    get_debug_log_writer,
    get_hook_counter_state,
    get_project_config,
    get_redis_client,
)
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.utils.project_config import ProjectConfig


def _request_with_state(**state_values):
    state = SimpleNamespace(**state_values)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


@pytest.fixture()
def project_config() -> ProjectConfig:
    env = {
        "LITELLM_CONFIG_PATH": "config.yaml",
        "REDIS_URL": "redis://localhost:6379/0",
        "LUTHIEN_POLICY_CONFIG": "policy.yaml",
    }
    return ProjectConfig(env_map=env)


def test_get_project_config_returns_instance(project_config: ProjectConfig):
    request = _request_with_state(project_config=project_config)
    assert get_project_config(request) is project_config


def test_get_project_config_requires_state():
    request = _request_with_state()
    with pytest.raises(RuntimeError):
        get_project_config(request)


def test_get_project_config_rejects_none(project_config: ProjectConfig):
    request = _request_with_state(project_config=None)
    with pytest.raises(RuntimeError):
        get_project_config(request)


def test_get_active_policy_returns_value():
    policy = NoOpPolicy()
    request = _request_with_state(active_policy=policy)
    assert get_active_policy(request) is policy


def test_get_active_policy_requires_state():
    request = _request_with_state()
    with pytest.raises(RuntimeError):
        get_active_policy(request)


def test_get_active_policy_rejects_none():
    request = _request_with_state(active_policy=None)
    with pytest.raises(RuntimeError):
        get_active_policy(request)


def test_get_hook_counter_state_returns_mapping():
    counters = Counter({"test": 1})
    request = _request_with_state(hook_counters=counters)
    assert get_hook_counter_state(request) is counters


def test_get_hook_counter_state_requires_state():
    request = _request_with_state()
    with pytest.raises(RuntimeError):
        get_hook_counter_state(request)


def test_get_hook_counter_state_rejects_none():
    request = _request_with_state(hook_counters=None)
    with pytest.raises(RuntimeError):
        get_hook_counter_state(request)


async def _noop_debug_writer(_: str, __: dict[str, object]) -> None:
    return None


def test_get_debug_log_writer_returns_callable():
    request = _request_with_state(debug_log_writer=_noop_debug_writer)
    writer = get_debug_log_writer(request)
    assert writer is _noop_debug_writer


def test_get_debug_log_writer_requires_state():
    request = _request_with_state()
    with pytest.raises(RuntimeError):
        get_debug_log_writer(request)


def test_get_debug_log_writer_rejects_none():
    request = _request_with_state(debug_log_writer=None)
    with pytest.raises(RuntimeError):
        get_debug_log_writer(request)


def test_get_database_pool_returns_value():
    pool = object()
    request = _request_with_state(database_pool=pool)
    assert get_database_pool(request) is pool


def test_get_database_pool_missing_attr_raises():
    request = _request_with_state()
    with pytest.raises(RuntimeError):
        get_database_pool(request)


def test_get_redis_client_returns_value():
    client = object()
    request = _request_with_state(redis_client=client)
    assert get_redis_client(request) is client


def test_get_redis_client_requires_state():
    request = _request_with_state()
    with pytest.raises(RuntimeError):
        get_redis_client(request)


def test_get_redis_client_rejects_none():
    request = _request_with_state(redis_client=None)
    with pytest.raises(RuntimeError):
        get_redis_client(request)
