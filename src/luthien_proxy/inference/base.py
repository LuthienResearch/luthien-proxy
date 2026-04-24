"""`InferenceProvider` abstract base and error hierarchy.

An `InferenceProvider` is a named, stateless-w.r.t.-requests instance that
resolves prompt + messages to a completion string. Concrete providers decide
how they authenticate, which backend they talk to, and which configured
default model to use.

Provider instances are constructed once with their config + shared service
references, then reused across many `complete()` calls. The registry added
in PR #3 will cache instances; for now providers are instantiated by their
caller.
"""

from __future__ import annotations

import abc
from typing import Any

from luthien_proxy.credentials.credential import Credential


class InferenceError(Exception):
    """Base class for errors raised by `InferenceProvider.complete()`.

    Callers (especially PR #4's fallback dispatcher) catch this class to
    distinguish inference failures from unrelated exceptions.
    """


class InferenceProviderError(InferenceError):
    """The backend returned an error we couldn't recover from.

    Covers non-credential errors: 5xx responses, unparseable output,
    unexpected subprocess exit codes, etc. The `message` field is safe to
    surface in logs; include the provider name + backend type for triage.
    """


class InferenceInvalidCredentialError(InferenceError):
    """The credential the provider used was rejected (401/403 or equivalent).

    Raised for both the configured server credential and for a
    `credential_override` value passed in at call time.
    """


class InferenceTimeoutError(InferenceError):
    """The backend did not respond before the configured timeout.

    Raised for both HTTP timeouts (DirectApiProvider) and subprocess
    timeouts (ClaudeCodeProvider).
    """


class InferenceCredentialOverrideUnsupported(InferenceError):
    """This provider backend cannot accept a `credential_override`.

    The canonical case is `ClaudeCodeProvider`: a user-supplied Anthropic
    API key or user OAuth token can't meaningfully authenticate the
    `claude` CLI against an *operator's* Claude subscription. PR #4's
    higher-level fallback logic catches this specifically and dispatches
    to a `DirectApiProvider` instead.
    """


class InferenceProvider(abc.ABC):
    """Abstract server-side inference provider.

    Subclasses are constructed once with their config + any shared service
    references. They must be stateless with respect to individual requests
    — no per-request mutable state on the provider instance.

    A provider has a human-readable `name` (surfaced in logs + future
    registry lookups) and a stable `backend_type` string that identifies
    the subclass family (e.g. `"claude_code"`, `"direct_api"`).
    """

    #: Stable identifier for the backend kind. Subclasses must override.
    backend_type: str = "abstract"

    def __init__(self, *, name: str) -> None:
        """Initialize with a human-readable provider name."""
        self.name = name

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format: dict[str, Any] | None = None,
        credential_override: Credential | None = None,
    ) -> str:
        """Run one completion turn and return the assistant message text.

        Args:
            messages: Chat-style message list, each `{"role": ..., "content": ...}`.
                `role` is one of `"user"`, `"assistant"`, or `"system"`. A
                `system` message in this list is equivalent to passing the
                `system` kwarg; if both are present, `system` wins.
            model: Override the provider's configured default model. Passing
                `None` means "use the provider's default".
            system: System prompt. If both this and a system message in
                `messages` are provided, this wins.
            temperature: Sampling temperature. Some backends may not honor
                this — they log a debug warning and ignore it.
            max_tokens: Generation cap. Some backends may not honor this;
                see per-provider docstrings.
            response_format: Optional structured-output spec (e.g.
                `{"type": "json_object"}`).
            credential_override: When set, use this credential instead of
                the provider's configured credential. This is how
                user-credential passthrough flows through the provider
                layer without re-plumbing every policy. A provider that
                cannot support this path must raise
                `InferenceCredentialOverrideUnsupported`.

        Returns:
            The assistant's message content as a plain string.

        Raises:
            InferenceInvalidCredentialError: Credential was rejected.
            InferenceTimeoutError: Backend timed out.
            InferenceCredentialOverrideUnsupported: Provider can't accept
                `credential_override`.
            InferenceProviderError: Any other backend failure.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release any persistent resources held by the provider.

        Default is a no-op. Providers that hold long-lived HTTP clients or
        subprocess pools should override this.
        """
        return None

    def __repr__(self) -> str:
        """Short repr that doesn't leak credentials."""
        return f"{type(self).__name__}(name={self.name!r}, backend_type={self.backend_type!r})"
