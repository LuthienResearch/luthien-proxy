"""Tests for `DirectApiProvider`.

We mock `judge_completion` at the provider's import site so the provider's
translation logic (credential selection, system-prompt merging, error
mapping) is exercised without LiteLLM on the call path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError, Timeout

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.inference.base import (
    InferenceInvalidCredentialError,
    InferenceProviderError,
    InferenceTimeoutError,
)
from luthien_proxy.inference.direct_api import DirectApiProvider


def _api_key_cred(value: str = "sk-ant-server-key") -> Credential:
    return Credential(value=value, credential_type=CredentialType.API_KEY)


def _oauth_cred(value: str = "sk-ant-oat01-user-token") -> Credential:
    return Credential(value=value, credential_type=CredentialType.AUTH_TOKEN)


class TestCredentialSelection:
    """`credential_override` replaces the configured server credential."""

    @pytest.mark.asyncio
    async def test_uses_configured_credential_by_default(self):
        """Without override, the configured server credential is passed through."""
        server_cred = _api_key_cred("sk-ant-SERVER")
        provider = DirectApiProvider(
            name="judge",
            credential=server_cred,
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            result = await provider.complete(messages=[{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert mock_call.call_args.args[0] is server_cred

    @pytest.mark.asyncio
    async def test_override_replaces_configured_credential(self):
        """Passing `credential_override` wins for this call."""
        server_cred = _api_key_cred("sk-ant-SERVER")
        user_cred = _oauth_cred("sk-ant-oat01-USER")
        provider = DirectApiProvider(
            name="judge",
            credential=server_cred,
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                credential_override=user_cred,
            )
        # positional first arg
        assert mock_call.call_args.args[0] is user_cred


class TestModelAndSystemPrompt:
    """Per-call `model` overrides `default_model`; system prompt handling."""

    @pytest.mark.asyncio
    async def test_per_call_model_overrides_default(self):
        """Explicit `model` kwarg beats the provider's default_model."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-opus-4-7",
            )
        assert mock_call.call_args.kwargs["model"] == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_system_kwarg_prepended_as_system_message(self):
        """`system=` injects a leading system message."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                system="Be terse.",
            )
        passed_messages = mock_call.call_args.kwargs["messages"]
        assert passed_messages[0] == {"role": "system", "content": "Be terse."}
        assert passed_messages[1] == {"role": "user", "content": "hi"}

    @pytest.mark.asyncio
    async def test_system_kwarg_replaces_in_message_system(self):
        """`system=` wins over any pre-existing system message in `messages`."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await provider.complete(
                messages=[
                    {"role": "system", "content": "old-system"},
                    {"role": "user", "content": "hi"},
                ],
                system="new-system",
            )
        passed_messages = mock_call.call_args.kwargs["messages"]
        assert passed_messages[0]["content"] == "new-system"
        # old-system is dropped
        assert all(m["content"] != "old-system" for m in passed_messages)


class TestErrorTranslation:
    """LiteLLM exceptions are translated into the `InferenceError` hierarchy."""

    @pytest.mark.asyncio
    async def test_authentication_error_becomes_invalid_credential(self):
        """401-class LiteLLM errors → InferenceInvalidCredentialError."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        exc = AuthenticationError(
            message="nope",
            llm_provider="anthropic",
            model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=exc),
        ):
            with pytest.raises(InferenceInvalidCredentialError):
                await provider.complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_timeout_becomes_inference_timeout(self):
        """LiteLLM Timeout → InferenceTimeoutError."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        exc = Timeout(message="slow", model="claude-sonnet-4-6", llm_provider="anthropic")
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=exc),
        ):
            with pytest.raises(InferenceTimeoutError):
                await provider.complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_connection_error_becomes_provider_error(self):
        """LiteLLM APIConnectionError → InferenceProviderError."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        exc = APIConnectionError(
            message="boom",
            model="claude-sonnet-4-6",
            llm_provider="anthropic",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=exc),
        ):
            with pytest.raises(InferenceProviderError):
                await provider.complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_value_error_from_judge_becomes_provider_error(self):
        """judge_completion's ValueError on empty/missing content → InferenceProviderError."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=ValueError("no content")),
        ):
            with pytest.raises(InferenceProviderError):
                await provider.complete(messages=[{"role": "user", "content": "hi"}])


class TestApiBaseAndResponseFormat:
    """Pass-through of optional LiteLLM kwargs."""

    @pytest.mark.asyncio
    async def test_api_base_passed_through(self):
        """Configured `api_base` flows to judge_completion."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
            api_base="https://custom.example.com",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await provider.complete(messages=[{"role": "user", "content": "hi"}])
        assert mock_call.call_args.kwargs["api_base"] == "https://custom.example.com"

    @pytest.mark.asyncio
    async def test_response_format_passed_through(self):
        """Structured-output spec is passed as-is."""
        provider = DirectApiProvider(
            name="judge",
            credential=_api_key_cred(),
            default_model="claude-sonnet-4-6",
        )
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
            )
        assert mock_call.call_args.kwargs["response_format"] == {"type": "json_object"}
