"""Server-side inference providers.

This package defines `InferenceProvider`, the interface the proxy uses for
its own LLM calls (judges, policy-testing, any future proxy-internal
inference). These calls bypass the gateway and the active policy — they
are proxy-defined logic that policies depend on, so routing them back
through the policy pipeline would create a circular dependency.

Two backends are currently provided:

- `DirectApiProvider` — wraps the existing LiteLLM path. Used for
  API-key-backed server credentials and for user-credential passthrough
  (via the `credential_override` argument to `complete()`).
- `ClaudeCodeProvider` — spawns `claude -p --bare` as a subprocess,
  authenticated with an operator-provisioned OAuth access token. Lets the
  proxy leverage a Claude subscription for judge work without per-token
  API billing.

PR #3 of the inference-provider initiative will add a DB-backed registry
on top of these primitives. PR #4 will wire policy YAML to name providers
by key. PR #5 will surface named providers in the policy-testing UI.
"""

from .base import (
    InferenceCredentialOverrideUnsupported,
    InferenceError,
    InferenceInvalidCredentialError,
    InferenceProvider,
    InferenceProviderError,
    InferenceResult,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
)
from .claude_code import ClaudeCodeProvider
from .direct_api import DirectApiProvider
from .registry import (
    DEFAULT_BACKEND_FACTORIES,
    InferenceProviderRegistry,
    InferenceRegistryError,
    MissingCredentialError,
    ProviderNotFoundError,
    ProviderRecord,
    UnknownBackendTypeError,
)

__all__ = [
    "ClaudeCodeProvider",
    "DEFAULT_BACKEND_FACTORIES",
    "DirectApiProvider",
    "InferenceCredentialOverrideUnsupported",
    "InferenceError",
    "InferenceInvalidCredentialError",
    "InferenceProvider",
    "InferenceProviderError",
    "InferenceProviderRegistry",
    "InferenceRegistryError",
    "InferenceResult",
    "InferenceStructuredOutputError",
    "InferenceTimeoutError",
    "MissingCredentialError",
    "ProviderNotFoundError",
    "ProviderRecord",
    "UnknownBackendTypeError",
]
