"""Dependency injection container and FastAPI dependency functions."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.client import LLMClient
from luthien_proxy.observability.emitter import EventEmitterProtocol
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
    credential_manager: CredentialManager | None = field(default=None)
    enable_request_logging: bool = field(default=False)

    @property
    def policy(self) -> OpenAIPolicyInterface:
        """Get current active policy from policy manager."""
        current = self.policy_manager.current_policy
        if not isinstance(current, OpenAIPolicyInterface):
            raise HTTPException(
                status_code=500,
                detail=f"Current policy {type(current).__name__} does not implement OpenAIPolicyInterface",
            )
        return current

    def get_anthropic_policy(self) -> AnthropicPolicyInterface:
        """Get the current Anthropic policy.

        Raises:
            HTTPException: If current policy doesn't implement AnthropicPolicyInterface
        """
        current = self.policy_manager.current_policy
        if not isinstance(current, AnthropicPolicyInterface):
            raise HTTPException(
                status_code=500,
                detail=f"Current policy {type(current).__name__} does not implement AnthropicPolicyInterface",
            )
        return current


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


def get_anthropic_client(request: Request) -> AnthropicClient | None:
    """Get Anthropic client from dependencies.

    Returns None when ANTHROPIC_API_KEY is not configured. The route handler
    must check for passthrough credentials before using this client.
    """
    return get_dependencies(request).anthropic_client


def get_anthropic_policy(request: Request) -> AnthropicPolicyInterface:
    """Get current Anthropic policy from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Current Anthropic policy
    """
    return get_dependencies(request).get_anthropic_policy()


def get_credential_manager(request: Request) -> CredentialManager | None:
    """Get credential manager from dependencies."""
    return get_dependencies(request).credential_manager


async def require_credential_manager(
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
) -> CredentialManager:
    """Get credential manager, raising 503 if not available."""
    if credential_manager is None:
        raise HTTPException(status_code=503, detail="Credential manager not available")
    return credential_manager


__all__ = [
    "Dependencies",
    "get_dependencies",
    "get_db_pool",
    "get_redis_client",
    "get_llm_client",
    "get_emitter",
    "get_policy",
    "get_policy_manager",
    "get_api_key",
    "get_admin_key",
    "get_anthropic_client",
    "get_anthropic_policy",
    "get_credential_manager",
    "require_credential_manager",
]
