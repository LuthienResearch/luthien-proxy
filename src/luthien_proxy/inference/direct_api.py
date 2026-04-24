"""`DirectApiProvider` — HTTP-backed inference via the Anthropic SDK.

Backs any HTTP-style Anthropic-compatible endpoint. This is the workhorse
provider used:

- When the operator-provisioned credential is an API key rather than an
  OAuth access token for `claude -p`.
- Whenever `credential_override` is passed — i.e. the
  user-credential-passthrough path. Because user-supplied creds can't
  meaningfully auth the Claude Code subprocess, passthrough always goes
  through an HTTP client, and this provider is where that happens.

Structured output: when the caller passes
`response_format={"type": "json_schema", "schema": {...}}`, we emit a
single-tool ``tools`` + ``tool_choice={"type": "tool", ...}`` construction
against the Anthropic Messages API. The model's tool-use input is the
structured payload; it validates via `jsonschema` defensively to keep a
clear error path for schema-invalid outputs even though the model is
constrained to emit schema-matching JSON.

When no schema is supplied (either no `response_format` or
`{"type": "json_object"}`), we emit a plain completion and prepend a
system-side instruction pushing the model toward JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
import jsonschema

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.inference.base import (
    InferenceCredentialOverrideUnsupported,
    InferenceInvalidCredentialError,
    InferenceProvider,
    InferenceProviderError,
    InferenceResult,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
    extract_schema,
)
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import AnthropicRequest

logger = logging.getLogger(__name__)


#: Synthetic tool name used for structured-output enforcement. Anthropic's
#: tool-use mechanism requires a tool name + schema and guarantees the
#: model's tool input matches. We pick a single-tool forced-use shape so
#: the model's output is definitely our structured payload.
_STRUCTURED_OUTPUT_TOOL_NAME = "_structured_output"


class DirectApiProvider(InferenceProvider):
    """HTTP-backed Anthropic Messages API provider.

    Attributes:
        name: Human-readable provider name (for logs, registry).
        default_model: Model to use when `complete(model=...)` is omitted.
        api_base: Optional override for the Anthropic endpoint. Passed to
            the SDK via `base_url`.
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
    ) -> InferenceResult:
        """Run one completion via the Anthropic SDK. See `InferenceProvider.complete`."""
        credential = credential_override if credential_override is not None else self._credential
        resolved_model = model if model is not None else self._default_model

        schema = extract_schema(response_format)

        log_extra = {
            "inference_provider_name": self.name,
            "inference_backend_type": self.backend_type,
            "inference_model": resolved_model,
            "inference_credential_override": credential_override is not None,
            "inference_structured": schema is not None,
        }
        logger.debug("inference.direct_api.call", extra=log_extra)

        client = _build_client(credential, self._api_base)

        try:
            request = _build_request(
                model=resolved_model,
                messages=messages,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                schema=schema,
            )
            try:
                response = await client.complete(request)
            finally:
                # The client wraps a fresh AsyncAnthropic built per call
                # (credential varies by request). Close its pool so we don't
                # leak file descriptors when many judge calls run in a row.
                await client.close()
        except anthropic.AuthenticationError as exc:
            raise InferenceInvalidCredentialError(
                f"{self.name}: credential rejected by backend: {exc}",
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise InferenceTimeoutError(f"{self.name}: backend timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise InferenceProviderError(f"{self.name}: backend connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            # 401/403 already caught as AuthenticationError above; remaining
            # status errors (rate limit, 5xx, bad request) collapse into a
            # generic provider error — callers don't branch on them today.
            status = getattr(exc, "status_code", None)
            if status in (401, 403):
                raise InferenceInvalidCredentialError(
                    f"{self.name}: credential rejected ({status}): {exc}",
                ) from exc
            raise InferenceProviderError(
                f"{self.name}: backend returned status {status}: {exc}",
            ) from exc
        except InferenceCredentialOverrideUnsupported:
            # Defensive: DirectApiProvider supports override, but don't mask
            # this sentinel type if something upstream raised it.
            raise
        except ValueError as exc:
            # _extract_structured / _extract_text raise ValueError for
            # malformed responses.
            raise InferenceProviderError(f"{self.name}: malformed backend response: {exc}") from exc

        if schema is not None:
            structured = _extract_structured(self.name, response, schema)
            return InferenceResult.from_structured(structured)

        return InferenceResult.from_text(_extract_text(response))


def _build_client(credential: Credential, api_base: str | None) -> AnthropicClient:
    """Build an `AnthropicClient` for a single request.

    The Anthropic SDK client is keyed by credential, so we can't share one
    instance across requests with different `credential_override` values.
    Building per-call is cheap (httpx.AsyncClient pool init only).
    """
    if credential.credential_type == CredentialType.AUTH_TOKEN:
        return AnthropicClient(auth_token=credential.value, base_url=api_base)
    return AnthropicClient(api_key=credential.value, base_url=api_base)


def _build_request(
    *,
    model: str,
    messages: list[dict[str, str]],
    system: str | None,
    temperature: float,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    schema: dict[str, Any] | None,
) -> AnthropicRequest:
    """Produce an `AnthropicRequest` for `AnthropicClient.complete()`.

    If a schema is present we encode a single-tool forced-use shape so the
    model's output is guaranteed to be tool_use with our schema. Without a
    schema, a `{"type": "json_object"}` hint collapses to a prompt-level
    instruction (the Anthropic API has no response-format JSON-object
    parameter — it's a text hint only).
    """
    effective_system = _compose_system(messages, system, schema, response_format)
    assistant_messages = [m for m in messages if m.get("role") != "system"]

    request: dict[str, Any] = {
        "model": model,
        "messages": assistant_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if effective_system is not None:
        request["system"] = effective_system

    if schema is not None:
        request["tools"] = [
            {
                "name": _STRUCTURED_OUTPUT_TOOL_NAME,
                "description": (
                    "Return the structured response. Populate input strictly according to the provided input_schema."
                ),
                "input_schema": schema,
            }
        ]
        request["tool_choice"] = {"type": "tool", "name": _STRUCTURED_OUTPUT_TOOL_NAME}

    return request  # type: ignore[return-value]


def _compose_system(
    messages: list[dict[str, str]],
    system: str | None,
    schema: dict[str, Any] | None,
    response_format: dict[str, Any] | None,
) -> str | None:
    """Produce the effective system prompt.

    Precedence: the `system` kwarg wins over any `system`-role message.
    For unstructured `json_object` requests we append a JSON-only
    instruction so the model doesn't emit prose.
    """
    if system is not None:
        effective: str | None = system
    else:
        existing = [m for m in messages if m.get("role") == "system"]
        effective = existing[0]["content"] if existing else None

    # Structured output uses tool-use; no prompt-side JSON hint needed (the
    # tool input_schema guarantees the shape).
    if schema is not None:
        return effective

    if response_format is not None and response_format.get("type") == "json_object":
        hint = "Respond with ONLY a JSON object. No prose, no markdown fences, no commentary."
        return f"{effective}\n\n{hint}" if effective else hint

    return effective


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of an `AnthropicResponse`.

    Concatenates all text blocks; skips tool_use blocks (unstructured calls
    shouldn't produce them, but if the model emits one anyway we don't want
    to crash — just ignore it).
    """
    content_blocks = response.get("content", [])
    parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                parts.append(text)
    if not parts:
        raise ValueError("Anthropic response has no text content")
    return "".join(parts)


def _extract_structured(
    provider_name: str,
    response: Any,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Pull a schema-valid dict out of a tool-use-constrained response.

    The request forced the model to call `_structured_output`; the tool
    input IS the structured payload. Defensively validate against the
    schema because tool-use invariants are a live area of SDK change —
    a regression there would surface here as a clean error rather than a
    corrupt payload downstream.
    """
    content_blocks = response.get("content", [])
    structured: dict[str, Any] | None = None
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            candidate = block.get("input")
            if isinstance(candidate, dict):
                structured = candidate
                break

    if structured is None:
        # The model may have refused and emitted text instead — try
        # parsing that as a last-resort JSON fallback.
        text = _try_extract_text(response)
        if text is not None:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    structured = parsed
            except json.JSONDecodeError:
                pass

    if structured is None:
        raise InferenceStructuredOutputError(
            f"{provider_name}: model produced no tool_use block and no parseable JSON text",
        )

    try:
        jsonschema.validate(instance=structured, schema=schema)
    except jsonschema.ValidationError as exc:
        raise InferenceStructuredOutputError(
            f"{provider_name}: model output failed schema validation: {exc.message}",
        ) from exc

    return structured


def _try_extract_text(response: Any) -> str | None:
    """Best-effort text extraction for the structured-output fallback."""
    try:
        return _extract_text(response)
    except ValueError:
        return None
