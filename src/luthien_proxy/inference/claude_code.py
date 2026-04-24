"""`ClaudeCodeProvider` — spawns `claude -p --bare` as a subprocess.

Lets the proxy drive Anthropic inference through an operator's Claude
subscription (OAuth access token) rather than a billed API key. See
`dev/NOTES.md` for the full experimental investigation that motivated this
design (including structured-output envelope shape).

Key decisions (verified experimentally, not assumed):

- We invoke `claude -p --bare --output-format json` so the CLI:
  - strips CLAUDE.md auto-discovery, hooks, skills, keychain reads, and
    plugin sync (keeps per-call input tokens to ~1.7k instead of ~50k);
  - treats `ANTHROPIC_API_KEY` or an `apiKeyHelper` as the only auth
    sources — no keychain, no OAuth-via-file.
- We pass the credential as `ANTHROPIC_API_KEY` in the child env. The
  `sk-ant-oat01-…` OAuth access token is accepted by this env slot. No
  temp files, no scratch config dir written.
- We isolate `HOME` and `CLAUDE_CONFIG_DIR` to a scratch directory anyway
  as belt-and-suspenders: bare mode doesn't read those paths, but a
  future CLI version might flip defaults.
- Structured output: when the caller passes
  `response_format={"type": "json_schema", "schema": {...}}`, we forward
  the schema to the CLI via `--json-schema <json>` and read
  `structured_output` from the JSON envelope. Envelope shape:

    {"subtype": "success", "is_error": false,
     "result": "",                       # free-form text (often empty)
     "structured_output": {...} | null,  # schema-valid object OR null
     ...}

    {"subtype": "error_max_structured_output_retries", "is_error": true,
     ...}

  We treat `structured_output: null` with no explicit error subtype as
  "model declined to produce structured output" and raise
  `InferenceStructuredOutputError` — this keeps the interface contract
  predictable even when the CLI silently shrugs. Callers that want
  lenient "structured or text" should not pass a schema.
- `max_tokens` and `temperature` remain unplumbed — the CLI has no flag
  for either in 2.1.x. Callers that need strict token control should use
  `DirectApiProvider`.

Multi-turn `messages` support is intentionally coarse: we render the
message list into a single prompt string with role markers. `stream-json`
input is available but requires `stream-json` output, which changes the
result shape. Not worth the complexity for PR #2.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from typing import Any

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

logger = logging.getLogger(__name__)

#: Default executable name. Overridable via constructor for tests.
DEFAULT_CLAUDE_BINARY = "claude"

#: Wall-clock timeout for a single `claude -p` invocation.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: CLI subtype emitted when the internal structured-output retry loop gives up.
#: Verified in the shipped claude binary (2.1.119).
STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE = "error_max_structured_output_retries"


class ClaudeCodeProvider(InferenceProvider):
    """Subscription-backed inference via the `claude` CLI.

    Attributes:
        name: Human-readable provider name.
        default_model: Model name passed to `--model`. If None, let the
            CLI pick (sonnet in bare mode as of 2.1.x).
        timeout_seconds: Wall-clock per-call timeout.
    """

    backend_type: str = "claude_code"

    def __init__(
        self,
        *,
        name: str,
        credential: Credential,
        default_model: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        claude_binary: str = DEFAULT_CLAUDE_BINARY,
    ) -> None:
        """Initialize with a stored credential and subprocess defaults.

        Args:
            name: Human-readable provider name for logs + registry.
            credential: The server-provisioned credential whose `value`
                will be injected as `ANTHROPIC_API_KEY` for the CLI
                subprocess. Must be an AUTH_TOKEN (OAuth access token);
                API keys work at the protocol level but using this
                provider for an API key wastes the subprocess overhead —
                use `DirectApiProvider` instead. Raises
                `ValueError` on API_KEY credential types to steer
                operators toward the right backend.
            default_model: Optional model for `--model`.
            timeout_seconds: Per-call wall-clock timeout.
            claude_binary: Executable name / path. Overridable for tests.
        """
        super().__init__(name=name)
        if credential.credential_type is not CredentialType.AUTH_TOKEN:
            raise ValueError(
                f"ClaudeCodeProvider {name!r}: credential must be AUTH_TOKEN "
                f"(OAuth access token), got {credential.credential_type.value!r}. "
                "Use DirectApiProvider for API-key-backed credentials."
            )
        self._credential = credential
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._claude_binary = claude_binary

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
        """Run one `claude -p` subprocess and return an `InferenceResult`.

        `temperature` and `max_tokens` are accepted for interface
        uniformity but are ignored (logged at DEBUG). `response_format`
        with a JSON schema is honored via `--json-schema`; see module
        docstring for envelope behavior and failure modes.
        """
        if credential_override is not None:
            raise InferenceCredentialOverrideUnsupported(
                f"ClaudeCodeProvider {self.name!r} cannot accept credential_override: "
                "user credentials do not authenticate the `claude` CLI against the "
                "operator's subscription. Route user-passthrough through DirectApiProvider."
            )

        resolved_model = model if model is not None else self._default_model
        prompt, system_prompt = _render_prompt(messages, system)
        schema = extract_schema(response_format)

        if temperature != 0.0:
            logger.debug(
                "inference.claude_code.ignored_temperature",
                extra={"inference_provider_name": self.name},
            )
        # max_tokens has a default value, so log that we're ignoring it only when
        # the caller probably cared (non-default). No reliable signal here — the
        # value has a default — so we don't emit a per-call warning.

        args = [self._claude_binary, "-p", "--bare", "--output-format", "json"]
        if resolved_model is not None:
            args.extend(["--model", resolved_model])
        if system_prompt is not None:
            args.extend(["--system-prompt", system_prompt])
        if schema is not None:
            args.extend(["--json-schema", json.dumps(schema, ensure_ascii=False)])
        args.append(prompt)

        scratch_dir = tempfile.mkdtemp(prefix="luthien-claude-")
        env = _build_child_env(self._credential.value, scratch_dir)

        logger.debug(
            "inference.claude_code.spawn",
            extra={
                "inference_provider_name": self.name,
                "inference_backend_type": self.backend_type,
                "inference_model": resolved_model,
                "inference_structured": schema is not None,
                "argv_preview": args[:5],  # no secrets — api key is via env, not argv
            },
        )

        try:
            stdout_bytes, stderr_bytes, returncode = await _run_subprocess(
                args,
                env=env,
                timeout_seconds=self._timeout_seconds,
            )
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

        return self._parse_output(
            stdout_bytes,
            stderr_bytes,
            returncode,
            structured_expected=schema is not None,
        )

    def _parse_output(
        self,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
        returncode: int,
        *,
        structured_expected: bool,
    ) -> InferenceResult:
        """Parse the JSON object from `claude -p`'s stdout.

        The CLI emits exactly one JSON object. Error shapes we translate:

        - Empty / unparseable stdout → `InferenceProviderError`.
        - `is_error: true`, `api_error_status ∈ {401, 403}` →
          `InferenceInvalidCredentialError`.
        - `is_error: true`, `subtype ==
          "error_max_structured_output_retries"` →
          `InferenceStructuredOutputError`.
        - Any other `is_error: true` → `InferenceProviderError`.
        - `is_error: false`, schema requested, `structured_output is None`
          → `InferenceStructuredOutputError` (the CLI silently returns
          None when the model declines the schema).

        Non-zero exit codes accompanying otherwise-valid JSON are still
        classified by the JSON body; exit code is informational only.
        """
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if not stdout:
            raise InferenceProviderError(
                f"{self.name}: claude -p produced empty stdout (exit={returncode}, stderr={stderr!r})",
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise InferenceProviderError(
                f"{self.name}: claude -p produced unparseable stdout "
                f"(exit={returncode}): {exc}. First 200 chars: {stdout[:200]!r}",
            ) from exc

        if not isinstance(payload, dict):
            raise InferenceProviderError(
                f"{self.name}: claude -p stdout was not a JSON object (got {type(payload).__name__})",
            )

        is_error = bool(payload.get("is_error"))
        subtype = payload.get("subtype")
        result_text = payload.get("result")
        structured_output = payload.get("structured_output")

        if is_error:
            api_status = payload.get("api_error_status")
            message = str(result_text) if result_text is not None else "unknown claude -p error"
            if api_status in (401, 403):
                raise InferenceInvalidCredentialError(
                    f"{self.name}: claude -p rejected credential (HTTP {api_status}): {message}",
                )
            if subtype == STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE:
                raise InferenceStructuredOutputError(
                    f"{self.name}: claude -p exhausted structured-output retries: {message}",
                )
            raise InferenceProviderError(
                f"{self.name}: claude -p returned error (subtype={subtype!r}, api_status={api_status}): {message}",
            )

        if structured_expected:
            if not isinstance(structured_output, dict):
                raise InferenceStructuredOutputError(
                    f"{self.name}: claude -p returned no structured_output for a "
                    f"schema-constrained request (model likely declined the schema). "
                    f"result preview: {str(result_text)[:200]!r}",
                )
            return InferenceResult.from_structured(structured_output)

        if not isinstance(result_text, str):
            raise InferenceProviderError(
                f"{self.name}: claude -p success payload missing string `result` field",
            )
        return InferenceResult.from_text(result_text)


async def _run_subprocess(
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
) -> tuple[bytes, bytes, int]:
    """Spawn the claude CLI, wait with a timeout, return (stdout, stderr, rc).

    Split out so tests can monkeypatch a single entry point. Raises
    `InferenceTimeoutError` on timeout; the caller handles JSON errors.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise InferenceTimeoutError(
            f"claude -p did not complete within {timeout_seconds:.0f}s",
        ) from exc
    return stdout_bytes, stderr_bytes, proc.returncode if proc.returncode is not None else -1


def _build_child_env(api_key: str, scratch_dir: str) -> dict[str, str]:
    """Minimal env for the claude subprocess.

    We inherit PATH (so the CLI can find node / its own deps) but otherwise
    scrub the parent environment: `--bare` is strict, and we don't want
    any hook/plugin envvar to accidentally sneak back in.
    """
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": scratch_dir,
        "CLAUDE_CONFIG_DIR": scratch_dir,
        "ANTHROPIC_API_KEY": api_key,
    }


def _render_prompt(messages: list[dict[str, str]], system: str | None) -> tuple[str, str | None]:
    """Flatten messages + optional system into (prompt, system_prompt).

    The CLI only accepts one positional prompt and one `--system-prompt`.
    We concatenate non-system messages into a labeled block:

        User: hi
        Assistant: hello
        User: goodbye

    System messages in `messages` are folded into the system prompt if the
    `system` kwarg isn't already provided; otherwise `system` wins and the
    in-message system blocks are dropped. This preserves the
    `InferenceProvider.complete` docstring contract.
    """
    system_from_messages: list[str] = []
    body_lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            system_from_messages.append(content)
            continue
        label = role.capitalize() if isinstance(role, str) else "User"
        body_lines.append(f"{label}: {content}")

    if system is not None:
        effective_system: str | None = system
    elif system_from_messages:
        effective_system = "\n\n".join(system_from_messages)
    else:
        effective_system = None

    prompt = "\n\n".join(body_lines) if body_lines else ""
    return prompt, effective_system
