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

Structured output: when the caller passes
`response_format={"type": "json_schema", "schema": {...}}`, we forward
the equivalent LiteLLM `{"type": "json_object"}` hint to nudge the model
toward JSON and then validate the returned text against the schema
post-hoc with `jsonschema`. Validation failures raise
`InferenceStructuredOutputError`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import jsonschema
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
    InferenceResult,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
    extract_schema,
    validate_schema,
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
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format: dict[str, Any] | None = None,
        credential_override: Credential | None = None,
    ) -> InferenceResult:
        """Run one completion via LiteLLM. See `InferenceProvider.complete`."""
        credential = credential_override if credential_override is not None else self._credential
        resolved_model = model if model is not None else self._default_model

        schema = extract_schema(response_format)
        if schema is not None:
            # Validate the schema itself (not the eventual response) before
            # spending a network round-trip. Catches malformed schemas that
            # would later surface as bare `jsonschema.SchemaError` and
            # size-exceeds-cap schemas that might overflow some backend's
            # request size. Uses the same helper as ClaudeCodeProvider so
            # the error shape is consistent.
            validate_schema(schema, self.name)

        effective_messages = _build_messages(self.name, messages, system, schema)
        litellm_response_format = _translate_response_format(response_format)

        log_extra = {
            "inference_provider_name": self.name,
            "inference_backend_type": self.backend_type,
            "inference_model": resolved_model,
            "inference_credential_override": credential_override is not None,
            "inference_structured": schema is not None,
        }
        logger.debug("inference.direct_api.call", extra=log_extra)

        # TODO(PR #4): LiteLLM exception types, its `json_schema → json_object`
        # collapse, and its `api_base` kwarg shape are all likely to change
        # when PR #4 absorbs judge_client.py into a shared helper. Update
        # the error-mapping block and `_translate_response_format` together
        # when that refactor lands.
        try:
            text = await judge_completion(
                credential,
                model=resolved_model,
                messages=effective_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_base=self._api_base,
                response_format=litellm_response_format,
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

        if schema is None:
            # Empty-text guard: a successful completion whose text is
            # whitespace-only is not useful for any downstream consumer.
            # Mirrors the same check in ClaudeCodeProvider.
            if not text.strip():
                raise InferenceProviderError(
                    f"{self.name}: backend returned empty response text",
                )
            return InferenceResult.from_text(text)

        # Structured-output path: parse + validate. We route through
        # `from_structured` so `.text` is the JSON-encoded form of
        # `.structured` — consistent with ClaudeCodeProvider. The raw
        # model text (which may be surrounding prose or an empty string
        # depending on the backend) is discarded; callers that asked for
        # structured output want the validated object, not the assistant's
        # wrapper text. `InferenceResult.text`'s docstring documents this.
        structured = _parse_and_validate(self.name, text, schema)
        return InferenceResult.from_structured(structured)


def _build_messages(
    provider_name: str,
    messages: list[dict[str, Any]],
    system: str | None,
    schema: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Produce the message list judge_completion should receive.

    If `system` is set, it takes precedence over any existing `system`
    message in `messages`: we drop the pre-existing one and prepend the
    provided system prompt.

    If a structured-output schema is set, we append a concise system-side
    instruction (either extending an existing system message or adding a
    new one) telling the model to emit JSON matching the schema. This is
    prompt-enforcement belt-and-suspenders around LiteLLM's json_object
    format hint: some backends don't honor `response_format` at all.

    Note: non-system messages flow through to LiteLLM unchanged — LiteLLM
    natively handles Anthropic-style list-of-blocks content. We only
    flatten the system-message content into a string when we need to
    concat a schema blurb onto it, because the blurb has to be serialized
    back into a single-string system value for cross-provider safety.
    """
    filtered = [m for m in messages if m.get("role") != "system"]
    if system is not None:
        effective_system: str | None = system
    else:
        existing = [m for m in messages if m.get("role") == "system"]
        if existing:
            effective_system = _coerce_system_content(provider_name, existing[0].get("content"))
        else:
            effective_system = None

    if schema is not None:
        schema_blurb = (
            "Respond with ONLY a JSON object conforming to this JSON Schema. "
            "No prose, no markdown fences, no commentary. Schema: " + json.dumps(schema, ensure_ascii=False)
        )
        if effective_system:
            effective_system = f"{effective_system}\n\n{schema_blurb}"
        else:
            effective_system = schema_blurb

    if effective_system is None:
        return filtered
    return [{"role": "system", "content": effective_system}, *filtered]


def _coerce_system_content(provider_name: str, content: Any) -> str:
    """Flatten a system-message content into a string for schema-blurb concat.

    - `str` → returned as-is.
    - `list` of Anthropic text blocks → concatenated `text` fields.
    - `list` containing a non-`text` block (image, tool_use, tool_result,
      etc.) → `InferenceProviderError`. Consistent with
      `ClaudeCodeProvider._content_to_text`: fail loudly rather than
      silently dropping the block and producing surprising behavior
      (system prompt unexpectedly empty, or downstream validation
      failures that the operator can't trace).
    - Anything else (int, dict, None, etc.) → `InferenceProviderError`.
      Same rationale: better to reject at call-site boundary than turn
      silently into a string that confuses later stages.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                raise InferenceProviderError(
                    f"{provider_name}: system-message content list contains a non-dict block "
                    f"({type(block).__name__}); only Anthropic-shaped text blocks are supported",
                )
            block_type = block.get("type")
            if block_type != "text":
                raise InferenceProviderError(
                    f"{provider_name}: unsupported system content block type {block_type!r}; "
                    "DirectApiProvider currently handles only text blocks. "
                    "Multi-modal/tool-use support is out of scope for PR #2.",
                )
            text = block.get("text", "")
            if not isinstance(text, str):
                raise InferenceProviderError(
                    f"{provider_name}: system text block has non-string text field ({type(text).__name__})",
                )
            parts.append(text)
        return "".join(parts)
    raise InferenceProviderError(
        f"{provider_name}: unsupported system message content type {type(content).__name__}; "
        "expected str or list of Anthropic text blocks",
    )


def _translate_response_format(
    response_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Translate our caller-facing shape into LiteLLM's `response_format` shape.

    We accept:

    - `None` or absent → no hint.
    - `{"type": "json_object"}` → pass through as-is.
    - `{"type": "json_schema", "schema": {...}}` → collapse to
      `{"type": "json_object"}` because LiteLLM's `json_schema` variant
      expects a name+schema wrapper that's different across providers.
      We handle schema validation ourselves post-hoc, so the hint only
      needs to steer the model to JSON.
    """
    if response_format is None:
        return None
    fmt_type = response_format.get("type")
    if fmt_type == "json_object":
        return {"type": "json_object"}
    if fmt_type == "json_schema":
        return {"type": "json_object"}
    return None


def _parse_and_validate(provider_name: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse `text` as JSON and validate against `schema`.

    Raises `InferenceStructuredOutputError` with a short, log-safe
    message on parse failure or schema-validation failure. The model's
    raw text is truncated in the error for log hygiene — full text is
    still reachable via exception chaining (`__cause__`).
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InferenceStructuredOutputError(
            f"{provider_name}: model did not return valid JSON for schema-constrained "
            f"response_format (first 200 chars: {text[:200]!r}): {exc}",
        ) from exc

    if not isinstance(obj, dict):
        raise InferenceStructuredOutputError(
            f"{provider_name}: model returned JSON but top-level was {type(obj).__name__}, expected object",
        )

    try:
        jsonschema.validate(instance=obj, schema=schema)
    except jsonschema.ValidationError as exc:
        raise InferenceStructuredOutputError(
            f"{provider_name}: model JSON failed schema validation: {exc.message}",
        ) from exc

    return obj
