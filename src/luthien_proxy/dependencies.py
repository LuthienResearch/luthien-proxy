"""Dependency injection container and FastAPI dependency functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis

from luthien_proxy.config_registry import ConfigRegistry
from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.observability.event_publisher import EventPublisherProtocol
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
)
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.utils import db
from luthien_proxy.webhook.sender import WebhookSender


@dataclass
class Dependencies:
    """Central container for all application dependencies.

    This container is created during app startup and stored in app.state.
    It provides type-safe access to all external services and allows easy
    mocking for tests.
    """

    db_pool: db.DatabasePool | None
    redis_client: Redis | None
    policy_manager: PolicyManager
    emitter: EventEmitterProtocol
    api_key: str | None
    admin_key: str | None
    anthropic_client: AnthropicClient | None = field(default=None)
    event_publisher: EventPublisherProtocol | None = field(default=None)
    credential_manager: CredentialManager | None = field(default=None)
    enable_request_logging: bool = field(default=False)
    usage_collector: UsageCollector | None = field(default=None)
    config_registry: ConfigRegistry | None = field(default=None)
    last_credential_info: dict[str, Any] = field(default_factory=dict)
    webhook_sender: WebhookSender | None = field(default=None)

    def get_anthropic_policy(self) -> AnthropicExecutionInterface:
        """Get the current Anthropic policy.

        Raises:
            HTTPException: If current policy doesn't implement AnthropicExecutionInterface
        """
        current = self.policy_manager.current_policy
        if not isinstance(current, AnthropicExecutionInterface):
            raise HTTPException(
                status_code=500,
                detail=(f"Current policy {type(current).__name__} does not implement AnthropicExecutionInterface"),
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


def get_event_publisher(request: Request) -> EventPublisherProtocol | None:
    """Get event publisher from dependencies."""
    return get_dependencies(request).event_publisher


def get_emitter(request: Request) -> EventEmitterProtocol:
    """Get event emitter from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Event emitter instance (never None - uses NullEventEmitter if not configured)
    """
    return get_dependencies(request).emitter


def get_policy_manager(request: Request) -> PolicyManager:
    """Get policy manager from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        Policy manager instance
    """
    return get_dependencies(request).policy_manager


def get_api_key(request: Request) -> str | None:
    """Get API key from dependencies.

    Args:
        request: FastAPI request object

    Returns:
        API key string, or None in passthrough-only mode
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


def get_anthropic_policy(request: Request) -> AnthropicExecutionInterface:
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


def get_usage_collector(request: Request) -> UsageCollector | None:
    """Get usage telemetry collector from dependencies."""
    return get_dependencies(request).usage_collector


def get_config_registry(request: Request) -> ConfigRegistry | None:
    """Get config registry from dependencies."""
    return get_dependencies(request).config_registry


def get_webhook_sender(request: Request) -> WebhookSender | None:
    """Get webhook sender from dependencies."""
    return get_dependencies(request).webhook_sender


async def require_config_registry(
    config_registry: ConfigRegistry | None = Depends(get_config_registry),
) -> ConfigRegistry:
    """Get config registry, raising 503 if not available."""
    if config_registry is None:
        raise HTTPException(status_code=503, detail="Config registry not available")
    return config_registry


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
    "get_event_publisher",
    "get_emitter",
    "get_policy_manager",
    "get_api_key",
    "get_admin_key",
    "get_anthropic_client",
    "get_anthropic_policy",
    "get_credential_manager",
    "require_credential_manager",
    "get_usage_collector",
    "get_config_registry",
    "require_config_registry",
    "get_webhook_sender",
]
