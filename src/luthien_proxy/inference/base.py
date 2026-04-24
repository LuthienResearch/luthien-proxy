"""`InferenceProvider` abstract base, result type, and error hierarchy.

An `InferenceProvider` is a named, stateless-w.r.t.-requests instance that
resolves prompt + messages to a completion. Concrete providers decide how
they authenticate, which backend they talk to, and which configured default
model to use.

Provider instances are constructed once with their config + shared service
references, then reused across many `complete()` calls. The registry added
in PR #3 will cache instances; for now providers are instantiated by their
caller.

`complete()` returns an `InferenceResult`:

- `text` is always populated. For structured-mode calls where the backend
  returns ONLY a structured object with no accompanying text, we stringify
  the structured payload into `text` so callers that don't branch on
  structured/unstructured always have a usable string.
- `structured` is populated when the caller passed a `response_format` with
  a JSON schema AND the backend produced a schema-valid object. Otherwise
  it is `None`.
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass
from typing import Any

import jsonschema

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


class InferenceStructuredOutputError(InferenceError):
    """The caller asked for structured output and the backend couldn't produce it.

    Covers two cases that are operationally similar:

    - `ClaudeCodeProvider`: the CLI returns
      `subtype: "error_max_structured_output_retries"` (its internal
      retry loop gave up), OR returns an otherwise-successful payload
      with `structured_output: null` even though a schema was supplied.
    - `DirectApiProvider`: the model produced text that doesn't parse as
      JSON, or parses but fails `jsonschema.validate`.
    """


@dataclass(frozen=True)
class InferenceResult:
    """Return value of `InferenceProvider.complete()`.

    Attributes:
        text: Assistant message as a plain string. Always populated; for
            structured-only backends we stringify `structured` into here
            so non-structured-aware callers keep working.
        structured: Validated dict when the caller asked for structured
            output (`response_format={"type": "json_schema", "schema": ...}`)
            and the backend produced a schema-valid object. Otherwise
            `None`.

    Cross-provider invariant (convention, not structurally enforced):
    when `structured is not None`, `text == json.dumps(structured,
    ensure_ascii=False)` — i.e. `text` is the JSON encoding of
    `structured`, not arbitrary model-wrapper text. Both providers
    currently route structured success through `from_structured` to
    maintain this. There are unit tests in both
    `test_direct_api.py::TestStructuredTextConsistency` and
    `test_claude_code.py::TestStructuredOutput` that pin it.

    TODO(structural-check): if future providers bypass `from_structured`
    and start populating `text` + `structured` directly, add a
    `__post_init__` assertion here that raises if the invariant is
    violated. Not worth the runtime cost today with only two providers,
    both of which go through `from_structured`.
    """

    text: str
    structured: dict[str, Any] | None = None

    @classmethod
    def from_text(cls, text: str) -> "InferenceResult":
        """Build a text-only result (no structured payload)."""
        return cls(text=text, structured=None)

    @classmethod
    def from_structured(cls, structured: dict[str, Any]) -> "InferenceResult":
        """Build a structured result; `.text` is the JSON-encoded form of `structured`.

        We stringify via `json.dumps` with `sort_keys=False` so the order
        the model chose is preserved for human-readable logs. Callers
        that want the object access it via `.structured`.
        """
        return cls(text=json.dumps(structured, ensure_ascii=False), structured=structured)


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
    ) -> InferenceResult:
        """Run one completion turn and return an `InferenceResult`.

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
            response_format: Optional structured-output spec. We accept the
                Claude-Agent-SDK shape: `{"type": "json_schema", "schema": {...}}`.
                When set, providers attempt to emit a schema-valid object;
                failures raise `InferenceStructuredOutputError`. A
                `{"type": "json_object"}` shape (no schema) is also
                accepted and passed through as a format hint without
                post-hoc validation.
            credential_override: When set, use this credential instead of
                the provider's configured credential. This is how
                user-credential passthrough flows through the provider
                layer without re-plumbing every policy. A provider that
                cannot support this path must raise
                `InferenceCredentialOverrideUnsupported`.

        Returns:
            `InferenceResult` with `text` always populated and `structured`
            populated when the caller asked for and got structured output.

        Raises:
            InferenceInvalidCredentialError: Credential was rejected.
            InferenceTimeoutError: Backend timed out.
            InferenceCredentialOverrideUnsupported: Provider can't accept
                `credential_override`.
            InferenceStructuredOutputError: Structured-output was requested
                but the backend couldn't produce a schema-valid object.
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


def extract_schema(response_format: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pull the JSON schema out of a `response_format` if one is present.

    Returns the schema dict for `{"type": "json_schema", "schema": {...}}`
    and `None` otherwise (including for `{"type": "json_object"}`, which
    asks for JSON without a specific schema).
    """
    if response_format is None:
        return None
    if response_format.get("type") != "json_schema":
        return None
    schema = response_format.get("schema")
    if not isinstance(schema, dict):
        return None
    return schema


#: Upper bound on the serialized size of a schema we're willing to forward.
#: Rationale: picked 64 KB because (a) `claude -p --json-schema <string>`
#: passes the schema as a single argv entry, and most kernels cap argv at
#: ~128 KB total; 64 KB leaves headroom for the other args + prompt, (b)
#: real-world JSON Schemas for judge-style structured output are well under
#: a few KB — anything approaching 64 KB is a policy-authoring bug, not a
#: legitimate need. Raise `InferenceStructuredOutputError` before spawning
#: anything so the caller sees a typed error and we never risk an
#: `OSError: E2BIG` from the syscall.
MAX_SCHEMA_SERIALIZED_BYTES = 64 * 1024


def validate_schema(schema: dict[str, Any], provider_name: str) -> str:
    """Sanity-check a JSON schema before we hand it to a backend.

    Two checks, both cheap, both before any subprocess spawn or HTTP call:

    1. `jsonschema.Draft7Validator.check_schema` — catches malformed
       schemas (invalid keywords, wrong types, etc.). Without this,
       `DirectApiProvider` could leak a bare `SchemaError` and
       `ClaudeCodeProvider` could hand the CLI a schema that makes it
       hang (we observed this experimentally; see dev/NOTES.md §10).
    2. Serialized-size cap (`MAX_SCHEMA_SERIALIZED_BYTES`). Prevents
       `E2BIG` from `create_subprocess_exec` and bounds argv size.

    Returns the serialized JSON string (so callers don't re-encode).
    Raises `InferenceStructuredOutputError` on either failure.
    """
    try:
        jsonschema.Draft7Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise InferenceStructuredOutputError(
            f"{provider_name}: invalid JSON schema: {exc.message}",
        ) from exc

    try:
        serialized = json.dumps(schema, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise InferenceStructuredOutputError(
            f"{provider_name}: schema is not JSON-serializable: {exc}",
        ) from exc

    size = len(serialized.encode("utf-8"))
    if size > MAX_SCHEMA_SERIALIZED_BYTES:
        raise InferenceStructuredOutputError(
            f"{provider_name}: schema serialized size {size} bytes exceeds cap of {MAX_SCHEMA_SERIALIZED_BYTES} bytes",
        )

    return serialized
