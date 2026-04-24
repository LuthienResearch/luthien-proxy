"""Dispatch an `InferenceProviderRef` to a concrete provider + credential_override.

Judge policies declare an `inference_provider:` YAML field that parses to one
of three tagged shapes (`UserCredentials` / `Provider(name)` /
`UserThenProvider(name, on_fallback)`). At request time the policy calls
`resolve_inference_provider(ref, context, registry)` and gets back a ready
(provider, credential_override) pair to pass to `provider.complete()`.

Design notes:

- `UserCredentials`: the request must carry a user credential (set by the
  gateway's auth pipeline onto `PolicyContext.user_credential`). We return
  a passthrough provider (built fresh per call) plus the user credential
  as override. A passthrough provider is a plain `DirectApiProvider`
  configured with a placeholder credential; the override is the only
  credential it actually uses.
- `Provider(name)`: look the provider up in the registry. The registry
  returns a freshly-built instance with its credential already resolved,
  so we return it with `credential_override=None`.
- `UserThenProvider(name, on_fallback)`: if the request has a user cred,
  same as `UserCredentials`. If not, the `on_fallback` mode decides whether
  to reject the request, log+fallback, or silently fallback — all three
  fallbacks reach for the same registry lookup as `Provider(name)`.

The passthrough provider is built with a `default_model` matching the
caller's configured judge model because `DirectApiProvider.complete()` uses
the caller-supplied model when present; the default only matters if
`complete(model=None)` is called.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from luthien_proxy.credentials import (
    Credential,
    CredentialError,
    CredentialType,
    InferenceProviderRef,
    Provider,
    UserCredentials,
    UserThenProvider,
)
from luthien_proxy.inference.base import InferenceProvider
from luthien_proxy.inference.direct_api import DirectApiProvider

if TYPE_CHECKING:
    from luthien_proxy.inference.registry import InferenceProviderRegistry
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


#: Placeholder credential used when the caller will always pass a
#: `credential_override`. The `DirectApiProvider` records it as `_credential`
#: but never reads it on the passthrough path.
_PLACEHOLDER_CREDENTIAL = Credential(
    value="",
    credential_type=CredentialType.API_KEY,
    platform="anthropic",
)


@dataclass(frozen=True)
class DispatchResult:
    """Result of resolving an `InferenceProviderRef`.

    Attributes:
        provider: The `InferenceProvider` to call.
        credential_override: Credential to pass to `provider.complete()` via
            the `credential_override` kwarg, or `None` when the provider
            should use its own configured credential.
    """

    provider: InferenceProvider
    credential_override: Credential | None


async def resolve_inference_provider(
    ref: InferenceProviderRef,
    context: "PolicyContext",
    registry: "InferenceProviderRegistry | None",
    *,
    passthrough_default_model: str,
    passthrough_api_base: str | None = None,
    passthrough_name: str = "passthrough",
) -> DispatchResult:
    """Resolve a policy-declared `InferenceProviderRef` to a concrete call target.

    Args:
        ref: The parsed reference from the policy's YAML config.
        context: Current `PolicyContext`. Used to read
            `context.user_credential` for passthrough flows.
        registry: The `InferenceProviderRegistry`, or `None` if the gateway
            was started without a DB pool. Required whenever `ref` names
            a registered provider.
        passthrough_default_model: Default model for the passthrough
            `DirectApiProvider`. Actual call model normally comes from the
            caller via `complete(model=...)`; this is only used when the
            caller passes `model=None`.
        passthrough_api_base: Optional API base override for the passthrough
            provider. Mostly for tests; production leaves it unset.
        passthrough_name: Human-readable name for the passthrough provider,
            shown in logs. Defaults to the literal ``"passthrough"``.

    Raises:
        CredentialError: `UserCredentials` selected but the request has no
            user credential, or a `UserThenProvider` with
            ``on_fallback="fail"`` had no user credential.
        InferenceRegistryError (subclass): Registry lookup failed.
        RuntimeError: Named-provider selected but no registry was configured.
    """
    if isinstance(ref, UserCredentials):
        return _passthrough(
            context,
            passthrough_default_model=passthrough_default_model,
            passthrough_api_base=passthrough_api_base,
            passthrough_name=passthrough_name,
        )

    if isinstance(ref, Provider):
        if registry is None:
            raise RuntimeError(
                f"inference_provider references {ref.name!r} but no "
                "InferenceProviderRegistry is configured. "
                "Start the gateway with a DATABASE_URL so providers can be looked up."
            )
        provider = await registry.get(ref.name)
        return DispatchResult(provider=provider, credential_override=None)

    if isinstance(ref, UserThenProvider):
        if context.user_credential is not None:
            return _passthrough(
                context,
                passthrough_default_model=passthrough_default_model,
                passthrough_api_base=passthrough_api_base,
                passthrough_name=passthrough_name,
            )

        if ref.on_fallback == "fail":
            raise CredentialError(
                "No user credential on request and on_fallback='fail' was set. "
                f"Configure a server-side provider {ref.name!r} or switch to "
                "on_fallback='warn' / 'fallback'."
            )
        if ref.on_fallback == "warn":
            logger.warning(
                "No user credential on request; falling back to registered provider %r.",
                ref.name,
            )
        else:
            logger.debug(
                "No user credential; silently falling back to registered provider %r.",
                ref.name,
            )

        if registry is None:
            raise RuntimeError(
                f"inference_provider fallback references {ref.name!r} but no "
                "InferenceProviderRegistry is configured. "
                "Start the gateway with a DATABASE_URL so providers can be looked up."
            )
        provider = await registry.get(ref.name)
        return DispatchResult(provider=provider, credential_override=None)

    raise TypeError(f"Unknown inference-provider reference type: {type(ref).__name__}")


def _passthrough(
    context: "PolicyContext",
    *,
    passthrough_default_model: str,
    passthrough_api_base: str | None,
    passthrough_name: str,
) -> DispatchResult:
    """Build a throwaway `DirectApiProvider` that the caller will override."""
    if context.user_credential is None:
        raise CredentialError("No user credential on request context")
    provider = DirectApiProvider(
        name=passthrough_name,
        credential=_PLACEHOLDER_CREDENTIAL,
        default_model=passthrough_default_model,
        api_base=passthrough_api_base,
    )
    return DispatchResult(provider=provider, credential_override=context.user_credential)


__all__ = [
    "DispatchResult",
    "resolve_inference_provider",
]
