"""Tests for `DirectApiProvider`.

We mock `judge_completion` at the provider's import site so the provider's
translation logic (credential selection, system-prompt merging, error
mapping, structured-output validation) is exercised without LiteLLM on
the call path.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from litellm.exceptions import APIConnectionError, AuthenticationError, Timeout

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.inference.base import (
    MAX_SCHEMA_SERIALIZED_BYTES,
    InferenceInvalidCredentialError,
    InferenceProviderError,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
)
from luthien_proxy.inference.direct_api import DirectApiProvider

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "population": {"type": "integer"},
    },
    "required": ["city", "population"],
    "additionalProperties": False,
}


def _api_key_cred(value: str = "sk-ant-server-key") -> Credential:
    return Credential(value=value, credential_type=CredentialType.API_KEY)


def _oauth_cred(value: str = "sk-ant-oat01-user-token") -> Credential:
    return Credential(value=value, credential_type=CredentialType.AUTH_TOKEN)


def _provider() -> DirectApiProvider:
    return DirectApiProvider(
        name="judge",
        credential=_api_key_cred(),
        default_model="claude-sonnet-4-6",
    )


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
        assert result.text == "ok"
        assert result.structured is None
        assert mock_call.call_args.args[0] is server_cred

    @pytest.mark.asyncio
    async def test_override_replaces_configured_credential(self):
        """Passing `credential_override` wins for this call."""
        user_cred = _oauth_cred("sk-ant-oat01-USER")
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                credential_override=user_cred,
            )
        assert mock_call.call_args.args[0] is user_cred


class TestModelAndSystemPrompt:
    """Per-call `model` overrides `default_model`; system prompt handling."""

    @pytest.mark.asyncio
    async def test_per_call_model_overrides_default(self):
        """Explicit `model` kwarg beats the provider's default_model."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-opus-4-7",
            )
        assert mock_call.call_args.kwargs["model"] == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_system_kwarg_prepended_as_system_message(self):
        """`system=` injects a leading system message."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                system="Be terse.",
            )
        passed_messages = mock_call.call_args.kwargs["messages"]
        assert passed_messages[0] == {"role": "system", "content": "Be terse."}
        assert passed_messages[1] == {"role": "user", "content": "hi"}

    @pytest.mark.asyncio
    async def test_system_kwarg_replaces_in_message_system(self):
        """`system=` wins over any pre-existing system message in `messages`."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="ok"),
        ) as mock_call:
            await _provider().complete(
                messages=[
                    {"role": "system", "content": "old-system"},
                    {"role": "user", "content": "hi"},
                ],
                system="new-system",
            )
        passed_messages = mock_call.call_args.kwargs["messages"]
        assert passed_messages[0]["content"] == "new-system"
        assert all(m["content"] != "old-system" for m in passed_messages)


class TestErrorTranslation:
    """LiteLLM exceptions are translated into the `InferenceError` hierarchy."""

    @pytest.mark.asyncio
    async def test_authentication_error_becomes_invalid_credential(self):
        """401-class LiteLLM errors → InferenceInvalidCredentialError."""
        exc = AuthenticationError(message="nope", llm_provider="anthropic", model="claude-sonnet-4-6")
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=exc),
        ):
            with pytest.raises(InferenceInvalidCredentialError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_timeout_becomes_inference_timeout(self):
        """LiteLLM Timeout → InferenceTimeoutError."""
        exc = Timeout(message="slow", model="claude-sonnet-4-6", llm_provider="anthropic")
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=exc),
        ):
            with pytest.raises(InferenceTimeoutError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_connection_error_becomes_provider_error(self):
        """LiteLLM APIConnectionError → InferenceProviderError."""
        exc = APIConnectionError(message="boom", model="claude-sonnet-4-6", llm_provider="anthropic")
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=exc),
        ):
            with pytest.raises(InferenceProviderError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_value_error_from_judge_becomes_provider_error(self):
        """judge_completion's ValueError on empty/missing content → InferenceProviderError."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(side_effect=ValueError("no content")),
        ):
            with pytest.raises(InferenceProviderError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])


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
    async def test_json_object_passthrough(self):
        """`{"type":"json_object"}` is forwarded to LiteLLM unchanged."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value='{"ok": true}'),
        ) as mock_call:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
            )
        assert mock_call.call_args.kwargs["response_format"] == {"type": "json_object"}


class TestStructuredOutputSuccess:
    """When the model produces schema-valid JSON, we return it structured."""

    @pytest.mark.asyncio
    async def test_structured_output_returned(self):
        """Valid JSON matching schema flows back as `structured`."""
        model_output = json.dumps({"city": "Paris", "population": 2_161_000})
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value=model_output),
        ):
            result = await _provider().complete(
                messages=[{"role": "user", "content": "info about Paris"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        assert result.structured == {"city": "Paris", "population": 2_161_000}
        assert result.text == model_output

    @pytest.mark.asyncio
    async def test_schema_collapses_to_json_object_for_litellm(self):
        """We don't forward `json_schema` to LiteLLM — we validate ourselves."""
        model_output = json.dumps({"city": "Paris", "population": 2_161_000})
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value=model_output),
        ) as mock_call:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        assert mock_call.call_args.kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_schema_blurb_added_to_system_prompt(self):
        """Schema mode appends an instruction to the system prompt."""
        model_output = json.dumps({"city": "Paris", "population": 2_161_000})
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value=model_output),
        ) as mock_call:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                system="Be concise.",
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        sys_msg = mock_call.call_args.kwargs["messages"][0]
        assert sys_msg["role"] == "system"
        assert "Be concise." in sys_msg["content"]
        assert "JSON Schema" in sys_msg["content"]


class TestStructuredOutputFailure:
    """Parse and validation failures map to `InferenceStructuredOutputError`."""

    @pytest.mark.asyncio
    async def test_invalid_json_raises_structured_output_error(self):
        """Model returned prose instead of JSON → StructuredOutputError."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="I'd rather write a haiku"),
        ):
            with pytest.raises(InferenceStructuredOutputError, match="valid JSON"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )

    @pytest.mark.asyncio
    async def test_json_array_raises_structured_output_error(self):
        """Top-level non-object JSON → StructuredOutputError."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="[1, 2, 3]"),
        ):
            with pytest.raises(InferenceStructuredOutputError, match="top-level was list"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )

    @pytest.mark.asyncio
    async def test_schema_validation_failure_raises_structured_output_error(self):
        """Valid JSON but violates schema → StructuredOutputError."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value=json.dumps({"city": "Paris"})),
        ):
            with pytest.raises(InferenceStructuredOutputError, match="schema validation"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )


class TestSchemaPreFlightValidation:
    """Schemas are checked + size-capped before any backend call."""

    @pytest.mark.asyncio
    async def test_malformed_schema_rejected_without_call(self):
        """A malformed schema is rejected before judge_completion runs."""
        mock_call = AsyncMock(return_value="ok")
        with patch("luthien_proxy.inference.direct_api.judge_completion", new=mock_call):
            with pytest.raises(InferenceStructuredOutputError, match="invalid JSON schema"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": {"type": "notAType"}},
                )
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_oversized_schema_rejected_without_call(self):
        """A schema over the size cap is rejected before judge_completion runs."""
        huge = {
            "type": "object",
            "description": "x" * (MAX_SCHEMA_SERIALIZED_BYTES + 50),
        }
        mock_call = AsyncMock(return_value="ok")
        with patch("luthien_proxy.inference.direct_api.judge_completion", new=mock_call):
            with pytest.raises(InferenceStructuredOutputError, match="exceeds cap"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": huge},
                )
        mock_call.assert_not_called()


class TestEmptyResponseGuard:
    """A whitespace-only response is treated as a backend error, not silent success."""

    @pytest.mark.asyncio
    async def test_empty_text_raises_provider_error(self):
        """Empty text in the no-schema path → InferenceProviderError."""
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value="   \n\t "),
        ):
            with pytest.raises(InferenceProviderError, match="empty response"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])


class TestMultiBlockContent:
    """Anthropic-shaped list content in the system message flows through without corruption."""

    @pytest.mark.asyncio
    async def test_system_message_as_text_blocks_coerced(self):
        """A system message with list-of-text-blocks content gets concatenated, not repr'd."""
        model_output = json.dumps({"city": "Paris", "population": 2_161_000})
        with patch(
            "luthien_proxy.inference.direct_api.judge_completion",
            new=AsyncMock(return_value=model_output),
        ) as mock_call:
            await _provider().complete(
                messages=[
                    {
                        "role": "system",
                        "content": [
                            {"type": "text", "text": "Be "},
                            {"type": "text", "text": "concise."},
                        ],
                    },
                    {"role": "user", "content": "hi"},
                ],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        passed_system = mock_call.call_args.kwargs["messages"][0]["content"]
        assert "Be concise." in passed_system
        # Make sure we're not leaking a Python list repr.
        assert "{'text'" not in passed_system
