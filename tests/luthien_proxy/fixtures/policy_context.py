"""Test helper for constructing PolicyContext instances.

Previously this lived on the production class as ``PolicyContext.for_testing``.
It is test-only scaffolding, so it lives in the test tree instead. Import it as::

    from tests.luthien_proxy.fixtures.policy_context import make_policy_context
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from luthien_proxy.observability.emitter import NullEventEmitter
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.types import RawHttpRequest

if TYPE_CHECKING:
    from luthien_proxy.credential_manager import CredentialManager
    from luthien_proxy.credentials.credential import Credential
    from luthien_proxy.inference.registry import InferenceProviderRegistry
    from luthien_proxy.utils.policy_cache import PolicyCacheFactory


def make_policy_context(
    transaction_id: str = "test-txn",
    request: Any | None = None,
    raw_http_request: RawHttpRequest | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    user_credential: "Credential | None" = None,
    credential_manager: "CredentialManager | None" = None,
    inference_provider_registry: "InferenceProviderRegistry | None" = None,
    policy_cache_factory: "PolicyCacheFactory | None" = None,
) -> PolicyContext:
    """Create a PolicyContext suitable for unit tests.

    Uses NullEventEmitter so no external dependencies are required.

    Args:
        transaction_id: Transaction ID (defaults to "test-txn")
        request: Optional request object
        raw_http_request: Optional raw HTTP request data
        session_id: Optional session ID
        user_id: Optional user identity for tests exercising user-aware behavior
        user_credential: Optional credential for tests exercising auth
        credential_manager: Optional manager for tests exercising auth providers
        inference_provider_registry: Optional provider registry for tests
            exercising named-provider dispatch
        policy_cache_factory: Optional cache factory for tests exercising caching

    Returns:
        PolicyContext with null implementations for external services
    """
    return PolicyContext(
        transaction_id=transaction_id,
        request=request,
        emitter=NullEventEmitter(),
        raw_http_request=raw_http_request,
        session_id=session_id,
        user_id=user_id,
        user_credential=user_credential,
        credential_manager=credential_manager,
        inference_provider_registry=inference_provider_registry,
        policy_cache_factory=policy_cache_factory,
    )
