"""Tests for `DirectApiProvider`.

We mock `AnthropicClient` at the provider's import site so the provider's
translation logic (credential selection, system-prompt merging, error
mapping, structured-output validation, tool-use forcing) is exercised
without the HTTP layer on the call path.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.inference.base import (
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


def _provider(api_base: str | None = None) -> DirectApiProvider:
    return DirectApiProvider(
        name="judge",
        credential=_api_key_cred(),
        default_model="claude-sonnet-4-6",
        api_base=api_base,
    )


def _text_response(text: str) -> dict:
    """Shape of `AnthropicClient.complete()`'s return."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _tool_use_response(payload: dict) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_test",
                "name": "_structured_output",
                "input": payload,
            }
        ],
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _build_api_status_error(status: int, message: str = "fail") -> anthropic.APIStatusError:
    """Minimal `APIStatusError` the SDK accepts (it normally builds these from httpx responses)."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, text=json.dumps({"error": {"message": message}}))
    return anthropic.APIStatusError(message, response=response, body={"error": {"message": message}})


def _mock_client(response_body: dict) -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(return_value=response_body)
    client.close = AsyncMock()
    return client


def _mock_client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(side_effect=exc)
    client.close = AsyncMock()
    return client


class TestCredentialSelection:
    """`credential_override` replaces the configured server credential."""

    @pytest.mark.asyncio
    async def test_uses_configured_credential_by_default(self):
        """Without override, the configured server credential is used to build the client."""
        server_cred = _api_key_cred("sk-ant-SERVER")
        provider = DirectApiProvider(
            name="judge",
            credential=server_cred,
            default_model="claude-sonnet-4-6",
        )
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client(_text_response("ok"))
            result = await provider.complete(messages=[{"role": "user", "content": "hi"}])
        assert result.text == "ok"
        assert result.structured is None
        assert mock_build.call_args.args[0] is server_cred

    @pytest.mark.asyncio
    async def test_override_replaces_configured_credential(self):
        """Passing `credential_override` wins for this call."""
        user_cred = _oauth_cred("sk-ant-oat01-USER")
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client(_text_response("ok"))
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                credential_override=user_cred,
            )
        assert mock_build.call_args.args[0] is user_cred


class TestRequestShape:
    """Per-call `model` overrides; system prompt handling; tool-use forcing."""

    @pytest.mark.asyncio
    async def test_per_call_model_overrides_default(self):
        """Explicit `model` kwarg beats the provider's default_model."""
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            client = _mock_client(_text_response("ok"))
            mock_build.return_value = client
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-opus-4-7",
            )
        request = client.complete.call_args.args[0]
        assert request["model"] == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_system_kwarg_wins_over_message_system(self):
        """`system=` replaces any pre-existing system role message."""
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            client = _mock_client(_text_response("ok"))
            mock_build.return_value = client
            await _provider().complete(
                messages=[
                    {"role": "system", "content": "old-system"},
                    {"role": "user", "content": "hi"},
                ],
                system="new-system",
            )
        request = client.complete.call_args.args[0]
        assert request["system"] == "new-system"
        # The Anthropic SDK takes system as its own field, not as a message,
        # so it must not leak back into `messages`.
        assert all(m.get("role") != "system" for m in request["messages"])

    @pytest.mark.asyncio
    async def test_schema_forces_single_tool_use(self):
        """Schema-constrained calls send a single tool + forced tool_choice."""
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            client = _mock_client(_tool_use_response({"city": "Paris", "population": 2_161_000}))
            mock_build.return_value = client
            await _provider().complete(
                messages=[{"role": "user", "content": "info about Paris"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        request = client.complete.call_args.args[0]
        assert len(request["tools"]) == 1
        assert request["tools"][0]["input_schema"] == SIMPLE_SCHEMA
        assert request["tool_choice"]["type"] == "tool"
        # System hint is NOT added for schema mode — tool-use covers it.
        assert "system" not in request or "JSON" not in request.get("system", "")

    @pytest.mark.asyncio
    async def test_json_object_appends_system_hint(self):
        """`{"type":"json_object"}` adds a prompt-level JSON instruction (no tool forcing)."""
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            client = _mock_client(_text_response('{"ok": true}'))
            mock_build.return_value = client
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
            )
        request = client.complete.call_args.args[0]
        assert "tools" not in request
        assert "JSON" in request["system"]


class TestErrorTranslation:
    """Anthropic SDK exceptions are translated into the `InferenceError` hierarchy."""

    @pytest.mark.asyncio
    async def test_authentication_error_becomes_invalid_credential(self):
        """SDK AuthenticationError → InferenceInvalidCredentialError."""
        exc = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
        exc.message = "nope"
        exc.args = ("nope",)
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client_raising(exc)
            with pytest.raises(InferenceInvalidCredentialError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_timeout_becomes_inference_timeout(self):
        """SDK APITimeoutError → InferenceTimeoutError."""
        timeout_exc = anthropic.APITimeoutError(request=httpx.Request("POST", "https://example.com"))
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client_raising(timeout_exc)
            with pytest.raises(InferenceTimeoutError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_connection_error_becomes_provider_error(self):
        """SDK APIConnectionError → InferenceProviderError."""
        conn_exc = anthropic.APIConnectionError(request=httpx.Request("POST", "https://example.com"))
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client_raising(conn_exc)
            with pytest.raises(InferenceProviderError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_status_401_becomes_invalid_credential(self):
        """APIStatusError with 401 → InferenceInvalidCredentialError."""
        status_exc = _build_api_status_error(401)
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client_raising(status_exc)
            with pytest.raises(InferenceInvalidCredentialError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_status_500_becomes_provider_error(self):
        """APIStatusError with non-auth status → InferenceProviderError."""
        status_exc = _build_api_status_error(503, "overloaded")
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client_raising(status_exc)
            with pytest.raises(InferenceProviderError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])


class TestStructuredOutput:
    """Tool-use extraction + schema validation."""

    @pytest.mark.asyncio
    async def test_structured_output_extracted_from_tool_use(self):
        """Tool input becomes the `structured` payload."""
        payload = {"city": "Paris", "population": 2_161_000}
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client(_tool_use_response(payload))
            result = await _provider().complete(
                messages=[{"role": "user", "content": "info about Paris"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        assert result.structured == payload
        assert result.text == json.dumps(payload, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_missing_tool_use_falls_back_to_text_parse(self):
        """If model emits text-only, we try parsing it as JSON for defense-in-depth."""
        payload = {"city": "Paris", "population": 2_161_000}
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client(_text_response(json.dumps(payload)))
            result = await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        assert result.structured == payload

    @pytest.mark.asyncio
    async def test_schema_validation_failure_raises(self):
        """Tool input that violates schema → InferenceStructuredOutputError."""
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client(_tool_use_response({"city": "Paris"}))
            with pytest.raises(InferenceStructuredOutputError, match="schema"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )

    @pytest.mark.asyncio
    async def test_missing_tool_use_and_unparseable_text_raises(self):
        """No tool_use + prose → InferenceStructuredOutputError."""
        with patch("luthien_proxy.inference.direct_api._build_client") as mock_build:
            mock_build.return_value = _mock_client(_text_response("I'd rather write a haiku"))
            with pytest.raises(InferenceStructuredOutputError):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )
