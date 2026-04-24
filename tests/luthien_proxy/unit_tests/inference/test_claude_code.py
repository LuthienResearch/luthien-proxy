"""Tests for `ClaudeCodeProvider`.

Subprocess spawning is mocked. The real CLI is exercised only in the
optional `test_live_claude_roundtrip` test (skipped unless
`LUTHIEN_TEST_CLAUDE=1` is set and `claude` is on PATH).

TODO(ci-infra): The integration tests below are gated on
`LUTHIEN_TEST_CLAUDE=1` and therefore never execute in the default CI
matrix. A scheduled weekly CI job that flips this flag (with an
operator-provisioned OAuth token in a protected secret) is planned but
lives in a separate infra PR — tracked but not in scope here.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.inference.base import (
    MAX_SCHEMA_SERIALIZED_BYTES,
    InferenceCredentialOverrideUnsupported,
    InferenceInvalidCredentialError,
    InferenceProviderError,
    InferenceResult,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
)
from luthien_proxy.inference.claude_code import (
    ClaudeCodeProvider,
    _build_child_env,
    _redact_argv_for_log,
    _render_prompt,
)

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "population": {"type": "integer"},
    },
    "required": ["city", "population"],
    "additionalProperties": False,
}


def _oauth_cred(value: str = "sk-ant-oat01-testtoken") -> Credential:
    return Credential(value=value, credential_type=CredentialType.AUTH_TOKEN)


def _api_key_cred(value: str = "sk-ant-api03-apikey") -> Credential:
    return Credential(value=value, credential_type=CredentialType.API_KEY)


def _mock_run_result(
    *,
    result: str = "pong",
    structured_output: dict | None = None,
    is_error: bool = False,
    subtype: str = "success",
    api_error_status: int | None = None,
    returncode: int = 0,
    stderr: str = "",
):
    """Build the (stdout_bytes, stderr_bytes, returncode) tuple _run_subprocess returns."""
    payload: dict = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "api_error_status": api_error_status,
        "result": result,
    }
    if structured_output is not None or not is_error:
        payload["structured_output"] = structured_output
    return (json.dumps(payload).encode(), stderr.encode(), returncode)


def _provider(**overrides) -> ClaudeCodeProvider:
    kwargs: dict[str, Any] = dict(name="sub", credential=_oauth_cred())
    kwargs.update(overrides)
    return ClaudeCodeProvider(**kwargs)


class TestCredentialValidation:
    """Constructor rejects credential types the provider can't use."""

    def test_api_key_credential_rejected(self):
        """API_KEY credentials must go through DirectApiProvider, not here."""
        with pytest.raises(ValueError, match="AUTH_TOKEN"):
            ClaudeCodeProvider(name="sub", credential=_api_key_cred())

    def test_auth_token_credential_accepted(self):
        """AUTH_TOKEN (OAuth access token) is the supported shape."""
        provider = _provider()
        assert provider.backend_type == "claude_code"
        assert provider.name == "sub"


class TestCredentialOverrideDisallowed:
    """`credential_override` raises clearly — PR #4 routes to DirectApi."""

    @pytest.mark.asyncio
    async def test_override_raises_unsupported(self):
        """A user credential can't auth the CLI against the operator's sub."""
        with pytest.raises(InferenceCredentialOverrideUnsupported):
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                credential_override=_oauth_cred("sk-ant-oat01-someone-else"),
            )


class TestSubprocessInvocation:
    """Verify argv, env, and prompt rendering for the subprocess call."""

    @pytest.mark.asyncio
    async def test_invokes_with_bare_json_flags(self):
        """Every call uses `-p --bare --output-format json`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await _provider().complete(messages=[{"role": "user", "content": "hello"}])
        args = mock_run.call_args.args[0]
        assert "-p" in args
        assert "--bare" in args
        assert "--output-format" in args
        assert args[args.index("--output-format") + 1] == "json"

    @pytest.mark.asyncio
    async def test_api_key_injected_via_env_not_argv(self):
        """Credential value never appears in argv; only in env."""
        secret = "sk-ant-oat01-SECRETTOKEN"
        provider = _provider(credential=_oauth_cred(secret))
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await provider.complete(messages=[{"role": "user", "content": "hi"}])
        args = mock_run.call_args.args[0]
        env = mock_run.call_args.kwargs["env"]
        assert all(secret not in arg for arg in args)
        assert env["ANTHROPIC_API_KEY"] == secret

    @pytest.mark.asyncio
    async def test_scratch_home_and_config_dir(self):
        """HOME and CLAUDE_CONFIG_DIR are set to a scratch dir, not operator's home."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await _provider().complete(messages=[{"role": "user", "content": "hi"}])
        env = mock_run.call_args.kwargs["env"]
        assert env["HOME"] == env["CLAUDE_CONFIG_DIR"]
        assert env["HOME"] != os.environ.get("HOME", "")

    @pytest.mark.asyncio
    async def test_model_flag_forwarded(self):
        """Per-call `model` kwarg becomes `--model <name>`."""
        provider = _provider(default_model="claude-sonnet-4-6")
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await provider.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-opus-4-7",
            )
        args = mock_run.call_args.args[0]
        assert args[args.index("--model") + 1] == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_default_model_used_when_omitted(self):
        """Without an explicit model kwarg, the provider's default_model is passed."""
        provider = _provider(default_model="claude-sonnet-4-6")
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await provider.complete(messages=[{"role": "user", "content": "hi"}])
        args = mock_run.call_args.args[0]
        assert args[args.index("--model") + 1] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_no_model_flag_when_no_default_and_no_kwarg(self):
        """If neither default nor kwarg is set, we don't pass --model."""
        provider = _provider(default_model=None)
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await provider.complete(messages=[{"role": "user", "content": "hi"}])
        args = mock_run.call_args.args[0]
        assert "--model" not in args

    @pytest.mark.asyncio
    async def test_system_prompt_forwarded(self):
        """`system=` becomes `--system-prompt <str>`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                system="Terse.",
            )
        args = mock_run.call_args.args[0]
        assert args[args.index("--system-prompt") + 1] == "Terse."


class TestOutputParsing:
    """Parse the JSON body emitted by `claude -p --output-format json`."""

    @pytest.mark.asyncio
    async def test_success_returns_result_string(self):
        """`is_error:false` path returns `.result` verbatim in `text`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result(result="hello world")),
        ):
            out = await _provider().complete(messages=[{"role": "user", "content": "hi"}])
        assert out.text == "hello world"
        assert out.structured is None

    @pytest.mark.asyncio
    async def test_401_translates_to_invalid_credential(self):
        """`api_error_status:401` → `InferenceInvalidCredentialError`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(
                return_value=_mock_run_result(
                    result="Invalid API key · Fix external API key",
                    is_error=True,
                    api_error_status=401,
                ),
            ),
        ):
            with pytest.raises(InferenceInvalidCredentialError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_403_translates_to_invalid_credential(self):
        """403 maps to the same error class as 401."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(
                return_value=_mock_run_result(
                    result="Forbidden",
                    is_error=True,
                    api_error_status=403,
                ),
            ),
        ):
            with pytest.raises(InferenceInvalidCredentialError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_other_error_status_translates_to_provider_error(self):
        """5xx / unknown error statuses → `InferenceProviderError`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(
                return_value=_mock_run_result(
                    result="upstream 500",
                    is_error=True,
                    api_error_status=500,
                ),
            ),
        ):
            with pytest.raises(InferenceProviderError):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_empty_stdout_raises_provider_error(self):
        """No JSON on stdout = `InferenceProviderError` with context."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=(b"", b"boom", 127)),
        ):
            with pytest.raises(InferenceProviderError, match="empty stdout"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_unparseable_stdout_raises_provider_error(self):
        """Non-JSON stdout = `InferenceProviderError`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=(b"not-json garbage", b"", 0)),
        ):
            with pytest.raises(InferenceProviderError, match="unparseable"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_success_payload_missing_result_raises(self):
        """`is_error:false` but no `result` field → `InferenceProviderError`."""
        payload = {"type": "result", "is_error": False}
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=(json.dumps(payload).encode(), b"", 0)),
        ):
            with pytest.raises(InferenceProviderError, match="result"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_whitespace_only_result_raises_empty_response(self):
        """Post-condition: a whitespace-only result is treated as an error."""
        payload = {"type": "result", "is_error": False, "result": "   \n\t "}
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=(json.dumps(payload).encode(), b"", 0)),
        ):
            with pytest.raises(InferenceProviderError, match="empty response"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_error_messages_include_returncode(self):
        """Error messages carry the subprocess exit code for diagnosability."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=(b"not-json", b"", 42)),
        ):
            with pytest.raises(InferenceProviderError, match="exit=42"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])


class TestStructuredOutput:
    """`response_format` with a schema flows via `--json-schema` and parses `structured_output`."""

    @pytest.mark.asyncio
    async def test_json_schema_flag_forwarded(self):
        """Schema dict becomes a JSON-encoded `--json-schema` argument."""
        structured = {"city": "Paris", "population": 2_161_000}
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result(result="", structured_output=structured)),
        ) as mock_run:
            await _provider().complete(
                messages=[{"role": "user", "content": "Paris info"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        args = mock_run.call_args.args[0]
        assert json.loads(args[args.index("--json-schema") + 1]) == SIMPLE_SCHEMA

    @pytest.mark.asyncio
    async def test_structured_output_returned_in_result(self):
        """`structured_output` from envelope flows to `result.structured`.

        Cross-provider invariant: `.text` equals the JSON-encoded form of
        `.structured`, identical to what `DirectApiProvider` returns.
        """
        structured = {"city": "Paris", "population": 2_161_000}
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result(result="", structured_output=structured)),
        ):
            result = await _provider().complete(
                messages=[{"role": "user", "content": "Paris info"}],
                response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
            )
        assert result.structured == structured
        # .text is the serialized form of structured, matching DirectApiProvider.
        expected = InferenceResult.from_structured(structured)
        assert result.text == expected.text

    @pytest.mark.asyncio
    async def test_retry_exhausted_subtype_raises_structured_error(self):
        """CLI subtype `error_max_structured_output_retries` → `InferenceStructuredOutputError`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(
                return_value=_mock_run_result(
                    result="retry limit hit",
                    is_error=True,
                    subtype="error_max_structured_output_retries",
                ),
            ),
        ):
            with pytest.raises(InferenceStructuredOutputError, match="retries"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )

    @pytest.mark.asyncio
    async def test_structured_output_null_with_schema_raises(self):
        """Schema asked for, CLI returned null (model declined) → structured error."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result(result="a haiku", structured_output=None)),
        ):
            with pytest.raises(InferenceStructuredOutputError, match="no structured_output"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
                )

    @pytest.mark.asyncio
    async def test_no_schema_no_flag(self):
        """Without `response_format`, no `--json-schema` flag is emitted."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await _provider().complete(messages=[{"role": "user", "content": "hi"}])
        args = mock_run.call_args.args[0]
        assert "--json-schema" not in args

    @pytest.mark.asyncio
    async def test_json_object_without_schema_does_not_emit_flag(self):
        """`{"type":"json_object"}` has no schema — we don't pass `--json-schema`."""
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(return_value=_mock_run_result()),
        ) as mock_run:
            await _provider().complete(
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
            )
        args = mock_run.call_args.args[0]
        assert "--json-schema" not in args


class TestSchemaValidation:
    """Pre-spawn schema checks catch malformed/oversized schemas cleanly."""

    @pytest.mark.asyncio
    async def test_invalid_schema_raises_structured_output_error_no_spawn(self):
        """Malformed schema is rejected before we even try to spawn."""
        bad_schema = {"type": "notAType"}
        mock_run = AsyncMock(return_value=_mock_run_result())
        with patch("luthien_proxy.inference.claude_code._run_subprocess", new=mock_run):
            with pytest.raises(InferenceStructuredOutputError, match="invalid JSON schema"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": bad_schema},
                )
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_oversized_schema_raises_without_spawn(self):
        """Schema that serializes over the cap is rejected pre-spawn."""
        huge_schema = {
            "type": "object",
            "description": "x" * (MAX_SCHEMA_SERIALIZED_BYTES + 100),
        }
        mock_run = AsyncMock(return_value=_mock_run_result())
        with patch("luthien_proxy.inference.claude_code._run_subprocess", new=mock_run):
            with pytest.raises(InferenceStructuredOutputError, match="exceeds cap"):
                await _provider().complete(
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_schema", "schema": huge_schema},
                )
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_oserror_on_spawn_wrapped_as_provider_error(self):
        """A failing `create_subprocess_exec` bubbles up as `InferenceProviderError`.

        Covers the case where a schema sneaks through validation but
        argv still overflows (e.g. combined with a very long prompt).
        """
        with patch(
            "luthien_proxy.inference.claude_code.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError(7, "Argument list too long")),
        ):
            with pytest.raises(InferenceProviderError, match="spawn"):
                await _provider().complete(messages=[{"role": "user", "content": "hi"}])


class _FakeProcess:
    """A minimal stand-in for `asyncio.subprocess.Process`.

    Two independent controllable events let tests distinguish between
    "cleanup started" and "cleanup completed":

    - `started_event`: set when `communicate()` begins. Lets the test
      synchronize with subprocess startup without a sleep.
    - `unblock_wait_event`: controls when `wait()` (and `communicate()`)
      resolves. `kill()` is synchronous and only sets `kill_called`; it
      does NOT release `wait()`. The test fires `unblock_wait_event` to
      simulate the child actually exiting.

    This shape is what makes the double-cancel test discriminating:
    with the broken shield-only code, a second cancel pops out of the
    shield-await while the inner reap is still suspended inside
    `proc.wait()`, so `rmtree` fires with `returncode is None`. With
    the loop-with-shield fix, `rmtree` waits until `wait()` returns,
    so `returncode == -9`.

    `returncode` is set ONLY when `wait()` observes the unblock event.
    """

    def __init__(self) -> None:
        self.kill_called = False
        self.started_event = asyncio.Event()
        self.unblock_wait_event = asyncio.Event()
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        self.started_event.set()
        await self.unblock_wait_event.wait()
        return b"", b""

    def kill(self) -> None:
        # kill is synchronous: flip the flag, don't release wait. The
        # test fires `unblock_wait_event` when it wants wait to return.
        self.kill_called = True

    async def wait(self) -> int | None:
        await self.unblock_wait_event.wait()
        self.returncode = -9
        return self.returncode


class _CancellationHarness:
    """Bundles the patches the cancellation tests need into one spot.

    Provides a fake `create_subprocess_exec` returning `fake_proc`, a
    tracking `mkdtemp` that records issued scratch dirs, and a tracking
    `rmtree` that records what it saw about `fake_proc` state when it
    was called (so tests can assert kill happened BEFORE rmtree).
    """

    def __init__(self) -> None:
        import tempfile as _tempfile

        self.fake_proc = _FakeProcess()
        self.scratch_dirs: list[str] = []
        # State observed at the moment rmtree fires.
        self.rmtree_saw_kill_called: bool | None = None
        self.rmtree_saw_returncode: int | None = None
        self._real_mkdtemp = _tempfile.mkdtemp
        self._real_rmtree = shutil.rmtree

    def mkdtemp(self, *args, **kwargs) -> str:
        path = self._real_mkdtemp(*args, **kwargs)
        self.scratch_dirs.append(path)
        return path

    def rmtree(self, path, *args, **kwargs) -> None:
        # Snapshot the subprocess state at cleanup time so the test can
        # assert rmtree only ran AFTER kill+wait finished.
        self.rmtree_saw_kill_called = self.fake_proc.kill_called
        self.rmtree_saw_returncode = self.fake_proc.returncode
        self._real_rmtree(path, *args, **kwargs)

    async def create_subprocess_exec(self, *args, **kwargs):
        return self.fake_proc


class TestCancellationPath:
    """Caller-side cancellation must terminate the child before the scratch dir is removed.

    Ordering invariant under test: when `complete()` is cancelled, the
    scratch dir must not be removed until `proc.wait()` has returned.
    The fake's `wait()` only resolves when `unblock_wait_event` is set
    AND it sets `returncode = -9` after resolving, so tests can read
    `rmtree_saw_returncode == -9` as "cleanup completed before rmtree."
    `rmtree_saw_returncode is None` means rmtree fired while wait was
    still suspended — the orphan-child-during-scratch-teardown scenario.
    """

    @pytest.mark.asyncio
    async def test_cancel_mid_flight_kills_child_and_propagates(self):
        """Single cancel: reap completes (kill+wait) BEFORE rmtree runs."""
        h = _CancellationHarness()
        with (
            patch(
                "luthien_proxy.inference.claude_code.asyncio.create_subprocess_exec",
                new=h.create_subprocess_exec,
            ),
            patch("luthien_proxy.inference.claude_code.tempfile.mkdtemp", new=h.mkdtemp),
            patch("luthien_proxy.inference.claude_code.shutil.rmtree", new=h.rmtree),
        ):
            task = asyncio.create_task(
                _provider(timeout_seconds=30).complete(messages=[{"role": "user", "content": "hi"}]),
            )
            await h.fake_proc.started_event.wait()
            task.cancel()
            # The reap's `wait()` will block on unblock_wait_event until
            # we fire it. Give the except-handler time to enter the
            # reap loop, then release wait so the loop can complete.
            await asyncio.sleep(0)
            h.fake_proc.unblock_wait_event.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        # rmtree must have observed `returncode == -9`, meaning the
        # fake's `wait()` returned BEFORE rmtree ran. `kill_called=True`
        # is strictly weaker (only checks the synchronous kill call).
        assert h.rmtree_saw_kill_called is True
        assert h.rmtree_saw_returncode == -9, "rmtree fired while proc.wait() was still suspended — child not reaped"
        assert len(h.scratch_dirs) == 1
        assert not os.path.exists(h.scratch_dirs[0])

    @pytest.mark.asyncio
    async def test_double_cancel_still_reaps_child_before_cleanup(self):
        """Second cancel during the reap-await does NOT detach the reap.

        This is the devil-round-2 scenario. With a bare
        `await asyncio.shield(_terminate_and_wait(proc))`, the inner
        task is protected but the outer `await` is still cancellable.
        A second `task.cancel()` while the inner reap is suspended in
        `proc.wait()` raises `CancelledError` at the shield-await;
        `complete()`'s `finally` then runs `rmtree` against a still-
        running child whose `wait()` hasn't returned.

        Observable: in that broken flow, `rmtree_saw_returncode is
        None` because the fake's `wait()` sets `returncode = -9` only
        AFTER it resolves — and it hasn't resolved yet.

        The loop-with-shield fix keeps awaiting the inner task,
        swallowing further `CancelledError`s, until cleanup completes.
        Then rmtree fires and sees `returncode == -9`.
        """
        h = _CancellationHarness()
        with (
            patch(
                "luthien_proxy.inference.claude_code.asyncio.create_subprocess_exec",
                new=h.create_subprocess_exec,
            ),
            patch("luthien_proxy.inference.claude_code.tempfile.mkdtemp", new=h.mkdtemp),
            patch("luthien_proxy.inference.claude_code.shutil.rmtree", new=h.rmtree),
        ):
            task = asyncio.create_task(
                _provider(timeout_seconds=30).complete(messages=[{"role": "user", "content": "hi"}]),
            )
            await h.fake_proc.started_event.wait()
            # First cancel: drives the task into the except-BaseException
            # arm, which starts the reap loop and awaits the shield.
            task.cancel()
            # Let the cancellation propagate to the shield-await point.
            # One yield is enough: cancellation is delivered at the next
            # suspension in the task.
            await asyncio.sleep(0)
            # Second cancel: lands on the shield-await while the inner
            # reap is still suspended inside `proc.wait()`.
            task.cancel()
            # Let the second cancellation be delivered.
            await asyncio.sleep(0)
            # Now release `wait()`. With the fix, the reap loop has been
            # looping on the shield and is still alive, so this unblocks
            # it; `complete()` then unwinds and `rmtree` fires with
            # returncode=-9. With the broken code, the second cancel
            # already popped out of the shield-await, `complete()`
            # unwound, `rmtree` fired, and `rmtree_saw_returncode` is
            # None — firing this event just lets a detached inner task
            # finish in the background (after the fact).
            h.fake_proc.unblock_wait_event.set()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert h.rmtree_saw_kill_called is True
        assert h.rmtree_saw_returncode == -9, (
            "rmtree ran while the child's wait() was still suspended — "
            "the reap detached into the background (shield footgun)"
        )
        assert len(h.scratch_dirs) == 1
        assert not os.path.exists(h.scratch_dirs[0])

    @pytest.mark.asyncio
    async def test_timeout_kills_child(self):
        """Timeout path also calls kill+wait on the child."""
        h = _CancellationHarness()
        with patch(
            "luthien_proxy.inference.claude_code.asyncio.create_subprocess_exec",
            new=h.create_subprocess_exec,
        ):
            # Schedule wait to unblock shortly after the timeout arm starts
            # its cleanup, so we don't hang the test. The exact timing
            # doesn't matter for this test — we just need proc.wait()
            # to return eventually so complete() unwinds.
            async def _release_wait_after_timeout():
                await asyncio.sleep(0.1)
                h.fake_proc.unblock_wait_event.set()

            releaser = asyncio.create_task(_release_wait_after_timeout())
            with pytest.raises(InferenceTimeoutError):
                await _provider(timeout_seconds=0.05).complete(
                    messages=[{"role": "user", "content": "hi"}],
                )
            await releaser
        assert h.fake_proc.kill_called is True


class TestPromptRendering:
    """`_render_prompt` flattens messages into one prompt + optional system."""

    def test_user_messages_labeled(self):
        """Each non-system message becomes a role-labeled line."""
        prompt, sys = _render_prompt(
            "sub",
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            system=None,
        )
        assert "User: hi" in prompt
        assert "Assistant: hello" in prompt
        assert sys is None

    def test_in_message_system_folded_into_system_prompt(self):
        """A system message in the list moves to the system slot."""
        prompt, sys = _render_prompt(
            "sub",
            [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "hi"},
            ],
            system=None,
        )
        assert sys == "Be terse."
        assert "System" not in prompt
        assert "User: hi" in prompt

    def test_system_kwarg_wins_over_in_message_system(self):
        """Explicit `system=` beats an in-message system block."""
        _, sys = _render_prompt(
            "sub",
            [
                {"role": "system", "content": "OLD"},
                {"role": "user", "content": "hi"},
            ],
            system="NEW",
        )
        assert sys == "NEW"


class TestMultiBlockContent:
    """Anthropic-shaped list content is unwrapped; unsupported block types fail clearly."""

    def test_text_block_list_concatenated(self):
        """A list of text blocks becomes the concatenated text."""
        prompt, _ = _render_prompt(
            "sub",
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello "},
                        {"type": "text", "text": "world"},
                    ],
                },
            ],
            system=None,
        )
        assert "User: hello world" in prompt
        # Make sure we're not leaking the block list's Python repr.
        assert "type" not in prompt
        assert "{'text'" not in prompt

    def test_non_text_block_raises_provider_error(self):
        """An image/tool-use block raises clearly — PR #2 is text-only."""
        with pytest.raises(InferenceProviderError, match="unsupported content block type"):
            _render_prompt(
                "sub",
                [
                    {
                        "role": "user",
                        "content": [{"type": "image", "source": {"data": "..."}}],
                    },
                ],
                system=None,
            )

    def test_non_dict_block_raises_provider_error(self):
        """A list containing non-dict items raises cleanly."""
        with pytest.raises(InferenceProviderError, match="non-dict block"):
            _render_prompt(
                "sub",
                [{"role": "user", "content": ["raw string in list"]}],
                system=None,
            )

    def test_non_string_content_raises_provider_error(self):
        """Neither str nor list content → typed error, not silent repr."""
        with pytest.raises(InferenceProviderError, match="unsupported message content type"):
            _render_prompt(
                "sub",
                [{"role": "user", "content": 42}],
                system=None,
            )


class TestBuildChildEnv:
    """Child env includes required keys plus an allowlist from parent env."""

    def test_required_keys_present(self):
        """HOME, CLAUDE_CONFIG_DIR, ANTHROPIC_API_KEY, PATH are always set."""
        env = _build_child_env("sk-ant-oat01-x", "/tmp/scratch")
        # We assert *required* keys rather than the complete set, so
        # extending the allowlist later doesn't break this test.
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-oat01-x"
        assert env["HOME"] == "/tmp/scratch"
        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/scratch"
        assert "PATH" in env and env["PATH"]

    def test_locale_env_propagated_when_set(self, monkeypatch):
        """LANG / LC_ALL / LC_* flow through when present in the parent env."""
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
        monkeypatch.setenv("LC_TIME", "C")
        env = _build_child_env("k", "/tmp/scratch")
        assert env.get("LANG") == "en_US.UTF-8"
        assert env.get("LC_ALL") == "en_US.UTF-8"
        assert env.get("LC_TIME") == "C"

    def test_tmpdir_propagated(self, monkeypatch):
        """TMPDIR is forwarded so the child can write scratch files consistently."""
        monkeypatch.setenv("TMPDIR", "/custom/tmp")
        env = _build_child_env("k", "/tmp/scratch")
        assert env.get("TMPDIR") == "/custom/tmp"

    def test_disallowed_env_var_not_forwarded(self, monkeypatch):
        """A random parent-env var the child shouldn't see is NOT copied."""
        monkeypatch.setenv("CLAUDE_CODE_ENABLE_TELEMETRY", "1")
        monkeypatch.setenv("SOME_OPERATOR_SECRET", "do-not-leak")
        env = _build_child_env("k", "/tmp/scratch")
        assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in env
        assert "SOME_OPERATOR_SECRET" not in env

    def test_scratch_overrides_any_parent_home(self, monkeypatch):
        """Even if HOME somehow ended up in the allowlist, our override wins."""
        monkeypatch.setenv("HOME", "/home/real-user")
        env = _build_child_env("k", "/tmp/scratch")
        assert env["HOME"] == "/tmp/scratch"


class TestArgvRedactor:
    """`_redact_argv_for_log` surfaces only safe flag names + values."""

    def test_prompt_never_included(self):
        """The positional prompt (last arg) is never in the redacted summary."""
        args = ["/usr/bin/claude", "-p", "--bare", "--output-format", "json", "my-secret-prompt"]
        summary = _redact_argv_for_log(args)
        assert all("my-secret-prompt" not in str(v) for v in summary.values())

    def test_schema_body_never_included(self):
        """Schema body is presence-only, never in the summary."""
        schema_str = json.dumps({"secret_internal_field": True})
        args = [
            "/usr/bin/claude",
            "-p",
            "--bare",
            "--json-schema",
            schema_str,
            "prompt",
        ]
        summary = _redact_argv_for_log(args)
        assert summary.get("json-schema_present") is True
        assert all(schema_str not in str(v) for v in summary.values())

    def test_system_prompt_value_never_included(self):
        """`--system-prompt` is presence-only."""
        args = [
            "/usr/bin/claude",
            "-p",
            "--bare",
            "--system-prompt",
            "secret-system-content",
            "user-prompt",
        ]
        summary = _redact_argv_for_log(args)
        assert summary.get("system-prompt_present") is True
        assert all("secret-system-content" not in str(v) for v in summary.values())

    def test_model_name_is_logged(self):
        """`--model` value is safe to log (it's a public model name)."""
        args = ["/usr/bin/claude", "-p", "--bare", "--model", "claude-opus-4-7", "prompt"]
        summary = _redact_argv_for_log(args)
        assert summary.get("model") == "claude-opus-4-7"

    def test_reordered_argv_does_not_leak(self):
        """Argv reorder: schema body still not logged."""
        args = [
            "/usr/bin/claude",
            "--json-schema",
            json.dumps({"hidden": True}),
            "-p",
            "--bare",
            "prompt",
        ]
        summary = _redact_argv_for_log(args)
        assert all("hidden" not in str(v) for v in summary.values())


# ---------------------------------------------------------------------------
# Optional live integration tests. Runs only when the operator opts in and
# `claude` is on PATH with a working subscription. Marked so they're skipped
# from the default `pytest` invocation.
#
# TODO(ci-infra): wire these into a scheduled CI job (weekly) so the real
# CLI contract is exercised outside of local operator boxes. Tracked as a
# separate infra PR.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("LUTHIEN_TEST_CLAUDE") != "1" or shutil.which("claude") is None,
    reason="Set LUTHIEN_TEST_CLAUDE=1 and have `claude` on PATH to run.",
)
@pytest.mark.asyncio
async def test_live_claude_roundtrip():
    """End-to-end: real `claude -p --bare` call with operator's OAuth token."""
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    assert os.path.exists(creds_path), "no credentials at ~/.claude/.credentials.json"
    with open(creds_path) as f:
        token = json.load(f)["claudeAiOauth"]["accessToken"]
    provider = ClaudeCodeProvider(
        name="live",
        credential=_oauth_cred(token),
        default_model="claude-sonnet-4-6",
        timeout_seconds=60.0,
    )
    out = await provider.complete(
        messages=[{"role": "user", "content": "Respond with exactly the word PONG."}],
        system="You follow instructions precisely.",
    )
    assert "PONG" in out.text.upper()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("LUTHIEN_TEST_CLAUDE") != "1" or shutil.which("claude") is None,
    reason="Set LUTHIEN_TEST_CLAUDE=1 and have `claude` on PATH to run.",
)
@pytest.mark.asyncio
async def test_live_claude_structured_output():
    """End-to-end: real `claude -p --bare --json-schema` with an OAuth token."""
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    assert os.path.exists(creds_path), "no credentials at ~/.claude/.credentials.json"
    with open(creds_path) as f:
        token = json.load(f)["claudeAiOauth"]["accessToken"]
    provider = ClaudeCodeProvider(
        name="live-struct",
        credential=_oauth_cred(token),
        default_model="claude-sonnet-4-6",
        timeout_seconds=60.0,
    )
    result = await provider.complete(
        messages=[{"role": "user", "content": "Return ONLY a JSON object for Paris, France. Just JSON."}],
        response_format={"type": "json_schema", "schema": SIMPLE_SCHEMA},
    )
    assert result.structured is not None
    assert "city" in result.structured
    assert "population" in result.structured
