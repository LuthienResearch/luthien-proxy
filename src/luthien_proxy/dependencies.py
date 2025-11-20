# ABOUTME: Dependency injection container for centralized service management
# ABOUTME: Provides type-safe access to Redis, DB, LLM client and other dependencies

"""Dependency injection container and FastAPI dependency functions."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.utils import db

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_protocol import PolicyProtocol


@dataclass
class Dependencies:
    """Central container for all application dependencies.

    This container is created during app startup and stored in app.state.
    It provides type-safe access to all external services and allows easy
    mocking for tests.
    """

    db_pool: db.DatabasePool | None
    redis_client: Redis | None
    llm_client: LLMClient
    policy_manager: PolicyManager
    api_key: str
    admin_key: str | None

    @cached_property
    def event_publisher(self) -> RedisEventPublisher | None:
        """Get or create event publisher from redis client.

        The event publisher is derived from the redis_client, so we only
        need to store one and create the other lazily.
        """
        if self.redis_client is None:
            return None
        return RedisEventPublisher(self.redis_client)

    @property
    def policy(self) -> PolicyProtocol:
        """Get current active policy from policy manager."""
        return self.policy_manager.current_policy


# === FastAPI Dependency Functions ===
# These can be used with FastAPI's Depends() for type-safe injection


def get_dependencies(request: Request) -> Dependencies:
    """Get the Dependencies container from app state.

    Args:
        request: FastAPI request object

    Returns:
        Dependencies container

    Raises:
        HTTPException: If dependencies not initialized
    """
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        raise HTTPException(
            status_code=500,
            detail="Dependencies not initialized",
        )
    return deps


def get_db_pool(request: Request) -> db.DatabasePool | None:
    """Get database pool from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Database pool or None if not connected
    """
    return get_dependencies(request).db_pool


def get_redis_client(request: Request) -> Redis | None:
    """Get Redis client from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Redis client or None if not connected
    """
    return get_dependencies(request).redis_client


def get_llm_client(request: Request) -> LLMClient:
    """Get LLM client from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        LLM client instance
    """
    return get_dependencies(request).llm_client


def get_event_publisher(request: Request) -> RedisEventPublisher | None:
    """Get event publisher from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Event publisher or None if Redis not connected
    """
    return get_dependencies(request).event_publisher


def get_policy(request: Request) -> PolicyProtocol:
    """Get current policy from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Current active policy
    """
    return get_dependencies(request).policy


def get_policy_manager(request: Request) -> PolicyManager:
    """Get policy manager from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Policy manager instance
    """
    return get_dependencies(request).policy_manager


def get_api_key(request: Request) -> str:
    """Get API key from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        API key string
    """
    return get_dependencies(request).api_key


def get_admin_key(request: Request) -> str | None:
    """Get admin API key from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Admin API key or None
    """
    return get_dependencies(request).admin_key


__all__ = [
    "Dependencies",
    "get_dependencies",
    "get_db_pool",
    "get_redis_client",
    "get_llm_client",
    "get_event_publisher",
    "get_policy",
    "get_policy_manager",
    "get_api_key",
    "get_admin_key",
]
