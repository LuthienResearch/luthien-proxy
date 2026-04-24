"""Tests for `ClaudeCodeProvider`.

Subprocess spawning is mocked. The real CLI is exercised only in the
optional `test_live_claude_roundtrip` test (skipped unless
`LUTHIEN_TEST_CLAUDE=1` is set and `claude` is on PATH).
"""

from __future__ import annotations

import json
import os
import shutil
from unittest.mock import AsyncMock, patch

import pytest

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.inference.base import (
    InferenceCredentialOverrideUnsupported,
    InferenceInvalidCredentialError,
    InferenceProviderError,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
)
from luthien_proxy.inference.claude_code import (
    ClaudeCodeProvider,
    _build_child_env,
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
    kwargs = dict(name="sub", credential=_oauth_cred())
    kwargs.update(overrides)
    return ClaudeCodeProvider(**kwargs)  # type: ignore[arg-type]


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
        fmt_idx = args.index("--output-format")
        assert args[fmt_idx + 1] == "json"

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
        idx = args.index("--model")
        assert args[idx + 1] == "claude-opus-4-7"

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
        idx = args.index("--json-schema")
        # The argument is a JSON-encoded string of our schema.
        assert json.loads(args[idx + 1]) == SIMPLE_SCHEMA

    @pytest.mark.asyncio
    async def test_structured_output_returned_in_result(self):
        """`structured_output` from envelope flows to `result.structured`."""
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
        # .text stringifies the structured object so unsophisticated callers still work.
        assert json.loads(result.text) == structured

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


class TestTimeout:
    """`InferenceTimeoutError` propagates from `_run_subprocess`."""

    @pytest.mark.asyncio
    async def test_timeout_from_run_subprocess(self):
        """Bubble up TimeoutError raised by the subprocess runner."""
        provider = _provider(timeout_seconds=0.01)
        with patch(
            "luthien_proxy.inference.claude_code._run_subprocess",
            new=AsyncMock(side_effect=InferenceTimeoutError("slow")),
        ):
            with pytest.raises(InferenceTimeoutError):
                await provider.complete(messages=[{"role": "user", "content": "hi"}])


class TestPromptRendering:
    """`_render_prompt` flattens messages into one prompt + optional system."""

    def test_user_messages_labeled(self):
        """Each non-system message becomes a role-labeled line."""
        prompt, sys = _render_prompt(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            system=None,
        )
        assert "User: hi" in prompt
        assert "Assistant: hello" in prompt
        assert sys is None

    def test_in_message_system_folded_into_system_prompt(self):
        """A system message in the list moves to the system slot."""
        prompt, sys = _render_prompt(
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
            [
                {"role": "system", "content": "OLD"},
                {"role": "user", "content": "hi"},
            ],
            system="NEW",
        )
        assert sys == "NEW"


class TestBuildChildEnv:
    """Child env contains only the keys we specify."""

    def test_exact_keys(self):
        """Only PATH, HOME, CLAUDE_CONFIG_DIR, ANTHROPIC_API_KEY are set."""
        env = _build_child_env("sk-ant-oat01-x", "/tmp/scratch")
        assert set(env.keys()) == {"PATH", "HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY"}
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-oat01-x"
        assert env["HOME"] == "/tmp/scratch"
        assert env["CLAUDE_CONFIG_DIR"] == "/tmp/scratch"


# ---------------------------------------------------------------------------
# Optional live integration test. Runs only when the operator opts in and
# `claude` is on PATH with a working subscription. Marked so it's skipped
# from the default `pytest` invocation.
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
