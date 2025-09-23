"""FastAPI dependency helpers for retrieving shared application state."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Coroutine, Optional, Protocol, cast

from fastapi import Request

from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.utils import db, redis_client
from luthien_proxy.utils.project_config import ProjectConfig

DebugLogWriter = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


class AppState(Protocol):
    """Describe the FastAPI app.state we expect to work with."""

    project_config: ProjectConfig | None
    active_policy: LuthienPolicy | None
    hook_counters: Counter[str] | None
    debug_log_writer: DebugLogWriter | None
    database_pool: db.DatabasePool | None
    redis_client: redis_client.RedisClient | None


def _require_app_state(request: Request) -> AppState:
    return cast(AppState, request.app.state)


def get_project_config(request: Request) -> ProjectConfig:
    """Return the ProjectConfig stored on app.state."""
    state = _require_app_state(request)
    try:
        config = state.project_config
    except AttributeError as exc:
        raise RuntimeError("ProjectConfig is not configured for this app instance") from exc
    if config is None:
        raise RuntimeError("ProjectConfig is not configured for this app instance")
    return config


def get_active_policy(request: Request) -> LuthienPolicy:
    """Return the active policy from app.state."""
    state = _require_app_state(request)
    try:
        policy = state.active_policy
    except AttributeError as exc:
        raise RuntimeError("Active policy not loaded for this app instance") from exc
    if policy is None:
        raise RuntimeError("Active policy not loaded for this app instance")
    return policy


def get_hook_counter_state(request: Request) -> Counter[str]:
    """Return the in-memory hook counters for this app."""
    state = _require_app_state(request)
    try:
        counters = state.hook_counters
    except AttributeError as exc:
        raise RuntimeError("Hook counters not initialized") from exc
    if counters is None:
        raise RuntimeError("Hook counters not initialized")
    return counters


def get_debug_log_writer(request: Request) -> DebugLogWriter:
    """Return the async debug log writer stored on app.state."""
    state = _require_app_state(request)
    try:
        writer = state.debug_log_writer
    except AttributeError as exc:
        raise RuntimeError("Debug log writer not configured") from exc
    if writer is None:
        raise RuntimeError("Debug log writer not configured")
    return writer


def get_database_pool(request: Request) -> Optional[db.DatabasePool]:
    """Return the configured database pool, if any."""
    state = _require_app_state(request)
    try:
        pool = state.database_pool
    except AttributeError as exc:
        raise RuntimeError("Database pool state missing on app") from exc
    return pool


def get_redis_client(request: Request) -> redis_client.RedisClient:
    """Return the cached redis client from app.state."""
    state = _require_app_state(request)
    try:
        client = state.redis_client
    except AttributeError as exc:
        raise RuntimeError("Redis client not configured") from exc
    if client is None:
        raise RuntimeError("Redis client not configured")
    return client


__all__ = [
    "AppState",
    "DebugLogWriter",
    "get_project_config",
    "get_active_policy",
    "get_hook_counter_state",
    "get_debug_log_writer",
    "get_database_pool",
    "get_redis_client",
]
