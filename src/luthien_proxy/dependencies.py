"""Dependency injection container and FastAPI dependency functions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.utils import db


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
    emitter: EventEmitterProtocol
    api_key: str
    admin_key: str | None
    anthropic_client: AnthropicClient | None = field(default=None)

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
    def policy(self) -> OpenAIPolicyInterface:
        """Get current active policy from policy manager."""
        return self.policy_manager.current_policy  # type: ignore[return-value]

    def get_anthropic_client(self) -> AnthropicClient:
        """Get or create the Anthropic client.

        Creates the client lazily on first access using ANTHROPIC_API_KEY
        from environment variables.

        Raises:
            HTTPException: If ANTHROPIC_API_KEY is not set
        """
        if self.anthropic_client is not None:
            return self.anthropic_client

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500,
                detail="ANTHROPIC_API_KEY not configured for native Anthropic path",
            )
        self.anthropic_client = AnthropicClient(api_key=api_key)
        return self.anthropic_client

    def get_anthropic_policy(self) -> AnthropicPolicyInterface:
        """Get the current Anthropic policy.

        Uses the policy from policy_manager if it implements AnthropicPolicyInterface,
        otherwise falls back to NoOpPolicy.

        Note: Not cached because policy can change at runtime via admin API.
        """
        # Check if the current policy supports Anthropic
        current = self.policy_manager.current_policy
        if isinstance(current, AnthropicPolicyInterface):
            return current

        # Fall back to NoOp if current policy doesn't support Anthropic
        return NoOpPolicy()


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


def get_emitter(request: Request) -> EventEmitterProtocol:
    """Get event emitter from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Event emitter instance (never None - uses NullEventEmitter if not configured)
    """
    return get_dependencies(request).emitter


def get_policy(request: Request) -> OpenAIPolicyInterface:
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


def get_anthropic_client(request: Request) -> AnthropicClient:
    """Get Anthropic client from dependencies.

    Creates the client lazily if not already initialized.

    Args:
        request: FastAPI request object

    Returns:
        Anthropic client instance

    Raises:
        HTTPException: If ANTHROPIC_API_KEY is not configured
    """
    return get_dependencies(request).get_anthropic_client()


def get_anthropic_policy(request: Request) -> AnthropicPolicyInterface:
    """Get current Anthropic policy from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Current Anthropic policy
    """
    return get_dependencies(request).get_anthropic_policy()


__all__ = [
    "Dependencies",
    "get_dependencies",
    "get_db_pool",
    "get_redis_client",
    "get_llm_client",
    "get_emitter",
    "get_event_publisher",
    "get_policy",
    "get_policy_manager",
    "get_api_key",
    "get_admin_key",
    "get_anthropic_client",
    "get_anthropic_policy",
]
