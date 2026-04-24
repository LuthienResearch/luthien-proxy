"""`DirectApiProvider` — thin wrapper around `llm.judge_client.judge_completion`.

Backs any HTTP-style LLM backend reachable via LiteLLM. This is the
workhorse provider used:

- When the operator-provisioned credential is an API key rather than an
  OAuth access token for `claude -p`.
- Whenever `credential_override` is passed — i.e. the
  user-credential-passthrough path. Because user-supplied creds can't
  meaningfully auth the Claude Code subprocess, passthrough always goes
  through an HTTP client, and this provider is where that happens.

We deliberately compose (not fork) `judge_completion`. PR #4 may absorb
both modules into a shared helper; for now the indirection is trivial.
"""

from __future__ import annotations

import logging
from typing import Any

from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    Timeout,
)

from luthien_proxy.credentials.credential import Credential
from luthien_proxy.inference.base import (
    InferenceCredentialOverrideUnsupported,
    InferenceInvalidCredentialError,
    InferenceProvider,
    InferenceProviderError,
    InferenceTimeoutError,
)
from luthien_proxy.llm.judge_client import judge_completion

logger = logging.getLogger(__name__)


class DirectApiProvider(InferenceProvider):
    """LiteLLM-backed HTTP inference provider.

    Attributes:
        name: Human-readable provider name (for logs, registry).
        default_model: Model to use when `complete(model=...)` is omitted.
        api_base: Optional override for the LLM endpoint (e.g. a custom
            OpenAI-compatible proxy). Passed through to LiteLLM.
        credential: The server credential used by default. Caller can
            override per-call via `credential_override`.
    """

    backend_type: str = "direct_api"

    def __init__(
        self,
        *,
        name: str,
        credential: Credential,
        default_model: str,
        api_base: str | None = None,
    ) -> None:
        """Initialize the provider with a configured server credential and default model."""
        super().__init__(name=name)
        self._credential = credential
        self._default_model = default_model
        self._api_base = api_base

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
        """Run one completion via LiteLLM. See `InferenceProvider.complete`."""
        credential = credential_override if credential_override is not None else self._credential
        resolved_model = model if model is not None else self._default_model

        effective_messages = _prepend_system(messages, system)

        log_extra = {
            "inference_provider_name": self.name,
            "inference_backend_type": self.backend_type,
            "inference_model": resolved_model,
            "inference_credential_override": credential_override is not None,
        }
        logger.debug("inference.direct_api.call", extra=log_extra)

        try:
            return await judge_completion(
                credential,
                model=resolved_model,
                messages=effective_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_base=self._api_base,
                response_format=response_format,
            )
        except AuthenticationError as exc:
            raise InferenceInvalidCredentialError(
                f"{self.name}: credential rejected by backend: {exc}",
            ) from exc
        except Timeout as exc:
            raise InferenceTimeoutError(f"{self.name}: backend timed out: {exc}") from exc
        except APIConnectionError as exc:
            raise InferenceProviderError(f"{self.name}: backend connection error: {exc}") from exc
        except InferenceCredentialOverrideUnsupported:
            # Defensive: DirectApiProvider supports override, but don't mask
            # this sentinel type if something upstream raised it.
            raise
        except ValueError as exc:
            # judge_completion raises ValueError for empty / malformed responses.
            raise InferenceProviderError(f"{self.name}: malformed backend response: {exc}") from exc


def _prepend_system(messages: list[dict[str, str]], system: str | None) -> list[dict[str, str]]:
    """Produce the message list judge_completion should receive.

    If `system` is set, it takes precedence over any existing `system`
    message in `messages`: we drop the pre-existing one and prepend the
    provided system prompt.
    """
    if system is None:
        return list(messages)
    filtered = [m for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": system}, *filtered]
