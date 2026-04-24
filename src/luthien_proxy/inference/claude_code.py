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
  `structured_output` from the JSON envelope.
- `max_tokens` and `temperature` remain unplumbed — the CLI has no flag
  for either in 2.1.x. Callers that need strict token control should use
  `DirectApiProvider`.

Subprocess lifecycle invariant (IMPORTANT):

Every `complete()` call must guarantee that the child `claude` process
has fully exited (wait() returned) BEFORE the scratch directory is
removed. Without this, caller-side `CancelledError` would leave the
subprocess running with the OAuth token in its env and no HOME/CLAUDE_CONFIG_DIR
to read from. `_run_subprocess` enforces this: it never returns without
`proc.wait()` having completed for the child, regardless of how it
unwinds (success, timeout, cancel, exception).
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
    validate_schema,
)

logger = logging.getLogger(__name__)

#: Default executable name. Overridable via constructor for tests.
DEFAULT_CLAUDE_BINARY = "claude"

#: Wall-clock timeout for a single `claude -p` invocation.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: CLI subtype emitted when the internal structured-output retry loop gives up.
#: Verified in the shipped claude binary (2.1.119).
STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE = "error_max_structured_output_retries"

#: Env vars that MUST pass through to the child. We don't want to inherit
#: the full parent env (hooks/plugin envvars would sneak back in, defeating
#: `--bare`), but a minimal PATH-only env breaks Node locale handling,
#: temp-file resolution, etc. Anything matching one of these exact names OR
#: any name starting with `LC_` is copied if set in the parent.
#:
#: We do NOT include `LANG`/`LC_*` in the "must have a default" set — if
#: the operator hasn't set them, that's fine, Node falls back to C.
_PASSTHROUGH_ENV_NAMES: frozenset[str] = frozenset(
    {
        "PATH",  # required — locate `claude` itself and its node runtime
        "LANG",  # locale for Node's Intl, console output
        "LC_ALL",
        "TMPDIR",  # temp-file location for child's intermediate writes
        "TEMP",  # Windows fallback; harmless on POSIX
        "TMP",  # ditto
    }
)


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
        messages: list[dict[str, Any]],
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
            # TODO(PR #4): the PR #4 dispatcher will catch this exception
            # and retry the call on a DirectApiProvider with the user's
            # credential. If that dispatch layer wants a non-exception
            # signal (e.g. `provider.supports_credential_override()`),
            # add it there — for now the exception is the contract.
            raise InferenceCredentialOverrideUnsupported(
                f"ClaudeCodeProvider {self.name!r} cannot accept credential_override: "
                "user credentials do not authenticate the `claude` CLI against the "
                "operator's subscription. Route user-passthrough through DirectApiProvider."
            )

        resolved_model = model if model is not None else self._default_model
        prompt, system_prompt = _render_prompt(self.name, messages, system)

        schema = extract_schema(response_format)
        serialized_schema: str | None = None
        if schema is not None:
            # Validate + enforce size cap BEFORE spawning anything. This
            # is the only way to avoid `OSError: E2BIG` on absurd schemas
            # and the CLI-hangs-indefinitely case for malformed schemas.
            serialized_schema = validate_schema(schema, self.name)

        if temperature != 0.0:
            logger.debug(
                "inference.claude_code.ignored_temperature",
                extra={"inference_provider_name": self.name},
            )

        args = [self._claude_binary, "-p", "--bare", "--output-format", "json"]
        if resolved_model is not None:
            args.extend(["--model", resolved_model])
        if system_prompt is not None:
            args.extend(["--system-prompt", system_prompt])
        if serialized_schema is not None:
            args.extend(["--json-schema", serialized_schema])
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
                # Redact by name, not index. The argv redactor surfaces a stable
                # set of flag names (never prompt content, never schema body,
                # never the model's system prompt) so a reorder can't leak.
                "argv_flags": _redact_argv_for_log(args),
            },
        )

        # _run_subprocess guarantees the child process has fully exited
        # (wait() returned) before it returns or re-raises, so the
        # scratch-dir rmtree below is always safe to run.
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
        - `is_error: false`, no schema, empty-after-strip result text →
          `InferenceProviderError` (post-condition guard).

        Non-zero exit codes accompanying otherwise-valid JSON are still
        classified by the JSON body; exit code is informational only.
        """
        # TODO: when we raise `InferenceProviderError`, include `returncode`
        # in every error message for diagnosability. Doing this piecemeal
        # below; a larger refactor to classify exit codes separately from
        # JSON-body errors is deferred to a follow-up PR.
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
                f"{self.name}: claude -p stdout was not a JSON object "
                f"(exit={returncode}, got {type(payload).__name__})",
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
                    f"{self.name}: claude -p rejected credential (HTTP {api_status}, exit={returncode}): {message}",
                )
            if subtype == STRUCTURED_OUTPUT_RETRY_EXHAUSTED_SUBTYPE:
                raise InferenceStructuredOutputError(
                    f"{self.name}: claude -p exhausted structured-output retries (exit={returncode}): {message}",
                )
            raise InferenceProviderError(
                f"{self.name}: claude -p returned error "
                f"(subtype={subtype!r}, api_status={api_status}, exit={returncode}): {message}",
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
                f"{self.name}: claude -p success payload missing string `result` field (exit={returncode})",
            )
        # Empty-text guard: a successful envelope with whitespace-only
        # `result` is not a useful answer for any downstream consumer.
        if not result_text.strip():
            raise InferenceProviderError(
                f"{self.name}: claude -p returned empty response text (exit={returncode})",
            )
        return InferenceResult.from_text(result_text)


async def _run_subprocess(
    args: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float,
) -> tuple[bytes, bytes, int]:
    """Spawn the claude CLI, wait with a timeout, return (stdout, stderr, rc).

    Invariant: this function never returns (whether normally or via
    exception) until `proc.wait()` has resolved for the child. That means
    callers are free to immediately clean up the child's working directory
    or env without risking a race with a still-running subprocess.

    Failure modes, all of which land on the same cleanup path:

    - Normal success: read stdout+stderr, await `proc.wait()` (it's
      already exited; `communicate()` ensures that). Return tuple.
    - `OSError` from `create_subprocess_exec` (`E2BIG`, binary missing,
      fork fails): re-raise as `InferenceProviderError`.
    - Timeout: `proc.kill()` → `await proc.wait()` →
      raise `InferenceTimeoutError`.
    - `asyncio.CancelledError` from the caller: `proc.kill()` →
      `await proc.wait()` (shielded so we ALWAYS wait even while
      cancelled) → re-raise `CancelledError`.
    - Any other exception while awaiting `communicate()`: `proc.kill()` →
      `await proc.wait()` → re-raise.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        # `E2BIG` (arg list too long), `ENOENT` (binary missing), fork
        # failures, etc. Wrap so PR #4's dispatch layer can catch as
        # `InferenceError` rather than a bare OSError.
        raise InferenceProviderError(
            f"failed to spawn `claude` subprocess: {exc}",
        ) from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        await _terminate_and_wait(proc)
        raise InferenceTimeoutError(
            f"claude -p did not complete within {timeout_seconds:.0f}s",
        ) from exc
    except asyncio.CancelledError:
        # Caller cancelled us. The contract is: cancellation propagates,
        # but we MUST fully reap the child before unwinding — otherwise
        # the subprocess keeps running with the OAuth token in /proc/<pid>/environ
        # and the scratch-dir cleanup in `complete()`'s finally races with
        # it reading HOME. Use `asyncio.shield` so the kill/wait isn't
        # itself interrupted by further cancellation attempts.
        await asyncio.shield(_terminate_and_wait(proc))
        raise
    except BaseException:
        # Any other bubble — propagate but still clean up the child.
        # `BaseException` catches `asyncio.CancelledError` in older
        # cooperating paths; we handle it explicitly above so this is for
        # SystemExit / KeyboardInterrupt / random exceptions.
        await asyncio.shield(_terminate_and_wait(proc))
        raise

    returncode = proc.returncode if proc.returncode is not None else -1
    return stdout_bytes, stderr_bytes, returncode


async def _terminate_and_wait(proc: asyncio.subprocess.Process) -> None:
    """Kill a running subprocess and wait for it to fully exit.

    Kill is idempotent (NOOP if the child already exited, which is the
    normal case after a successful `communicate()`). `wait()` reaps the
    zombie; without it, the child lingers in process-table-land.

    Split out so tests can mock it directly to verify kill+wait happened
    on the cancellation path.
    """
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            # Race: child exited between our check and kill. Fine.
            pass
    # `wait()` is safe to call repeatedly — if already reaped, returns
    # the cached returncode immediately.
    await proc.wait()


def _build_child_env(api_key: str, scratch_dir: str) -> dict[str, str]:
    """Minimal env for the claude subprocess.

    We start with a narrow allowlist from the parent env (see
    `_PASSTHROUGH_ENV_NAMES`) plus any `LC_*` locale variables, then layer
    on our required overrides (`HOME`, `CLAUDE_CONFIG_DIR` → scratch dir;
    `ANTHROPIC_API_KEY` → the credential). The allowlist balances two
    concerns:

    - `--bare` is strict and we don't want hook/plugin envvars to sneak
      back in (e.g. `CLAUDE_CODE_ENABLE_TELEMETRY`, `NODE_OPTIONS`).
    - Node needs `PATH` to find itself, often needs `LANG`/`LC_*` for
      Intl-related code paths, and respects `TMPDIR` for scratch files.

    If an operator sets these vars unusually (e.g. `LC_ALL=C.UTF-8`), that
    propagates — which is what they want.
    """
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        if name in _PASSTHROUGH_ENV_NAMES or name.startswith("LC_"):
            env[name] = value

    # Required overrides, applied LAST so they win over any accidental
    # parent-env match (e.g. if somehow HOME ended up in the allowlist).
    env["HOME"] = scratch_dir
    env["CLAUDE_CONFIG_DIR"] = scratch_dir
    env["ANTHROPIC_API_KEY"] = api_key
    # PATH is required; if the parent didn't have one, fall back to a
    # sane default rather than shipping an empty string.
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


# Flags whose names are safe to log. Values attached to these flags may still
# carry sensitive content (e.g. the user's prompt), so we log only the flag
# name itself and, for a bool-able subset, a non-sensitive bool summary.
_LOGGABLE_FLAG_NAMES: frozenset[str] = frozenset(
    {
        "-p",
        "--bare",
        "--output-format",
        "--model",
    }
)

# Flags whose presence we log as a bool (true/false) without the value, because
# the value is sensitive (schema body = caller code, system prompt = caller
# code, positional prompt = user content).
_PRESENCE_ONLY_FLAGS: frozenset[str] = frozenset(
    {
        "--system-prompt",
        "--json-schema",
    }
)


def _redact_argv_for_log(args: list[str]) -> dict[str, object]:
    """Build a redacted, allowlist-based summary of argv for structured logs.

    Returns a dict suitable for a `logger.debug(..., extra={...})` field
    rather than a slice. A slice is index-based: if the argv order ever
    changes (e.g. someone moves the prompt to a keyword flag), a slice
    that previously held flag names could silently leak the prompt.

    Rules:
    - Known-safe flag names (`-p`, `--bare`, etc.): include both the flag
      and its immediately-following value (e.g. `--model claude-sonnet-4-6`).
    - Presence-only flags (`--system-prompt`, `--json-schema`): log as a
      bool `"<flag>_present": True/False`, never the value.
    - The positional prompt (last arg): never logged from here.
    - Anything else: dropped.
    """
    summary: dict[str, object] = {
        "binary": os.path.basename(args[0]) if args else "",
    }
    i = 1
    for flag in _PRESENCE_ONLY_FLAGS:
        summary[f"{flag.lstrip('-')}_present"] = flag in args
    while i < len(args):
        arg = args[i]
        if arg in _LOGGABLE_FLAG_NAMES:
            # Bool-ish flags like `-p` / `--bare` take no value.
            if arg in {"-p", "--bare"}:
                summary[arg.lstrip("-")] = True
                i += 1
                continue
            # `--model` etc. take exactly one value.
            value = args[i + 1] if i + 1 < len(args) else ""
            summary[arg.lstrip("-")] = value
            i += 2
            continue
        # Skip presence-only flag values, handled above.
        if arg in _PRESENCE_ONLY_FLAGS:
            i += 2
            continue
        i += 1
    return summary


def _render_prompt(
    provider_name: str,
    messages: list[dict[str, Any]],
    system: str | None,
) -> tuple[str, str | None]:
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

    `content` values come in two shapes:

    - string: used verbatim.
    - Anthropic-shaped list-of-blocks: each block is `{"type": "text",
      "text": "..."}`. We concatenate the `text` fields. Non-`text` blocks
      (e.g. `image`, `tool_use`, `tool_result`) raise
      `InferenceProviderError` — future PRs can add richer support; for
      now we fail loudly rather than silently garbling.
    """
    system_from_messages: list[str] = []
    body_lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = _content_to_text(provider_name, msg.get("content", ""))
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


def _content_to_text(provider_name: str, content: Any) -> str:
    """Normalize message content into a plain string.

    - `str` → returned as-is.
    - `list[dict]` (Anthropic-style blocks) → concatenate `.text` fields
      of `text`-type blocks. Non-text block types raise a typed error.
    - Anything else → typed error. We never fall back to `str(content)`
      because that produces a Python repr that the model would see
      verbatim in the prompt.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                raise InferenceProviderError(
                    f"{provider_name}: message content list contains a non-dict block "
                    f"({type(block).__name__}); only Anthropic-shaped text blocks are supported",
                )
            block_type = block.get("type")
            if block_type != "text":
                raise InferenceProviderError(
                    f"{provider_name}: unsupported content block type {block_type!r}; "
                    "ClaudeCodeProvider currently handles only text blocks. "
                    "Multi-modal/tool-use support is out of scope for PR #2.",
                )
            text = block.get("text", "")
            if not isinstance(text, str):
                raise InferenceProviderError(
                    f"{provider_name}: text block has non-string text field ({type(text).__name__})",
                )
            parts.append(text)
        return "".join(parts)
    raise InferenceProviderError(
        f"{provider_name}: unsupported message content type {type(content).__name__}; "
        "expected str or list of Anthropic text blocks",
    )
