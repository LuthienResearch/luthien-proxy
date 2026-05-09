# ABOUTME: Unit tests for admin route handlers
# ABOUTME: Tests HTTP layer for policy management endpoints

"""Tests for admin route handlers.

These tests focus on the HTTP layer - ensuring routes properly:
- Handle dependency injection
- Convert service exceptions to appropriate HTTP status codes
- Return correct response models
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from luthien_proxy.admin.routes import (
    AuthConfigResponse,
    AuthConfigUpdateRequest,
    CachedCredentialsListResponse,
    ChatRequest,
    ChatResponse,
    PolicyEnableResponse,
    PolicySetRequest,
    ServerCredentialRequest,
    TelemetryConfigUpdateRequest,
    _config_to_response,
    delete_server_credential,
    get_auth_config,
    get_available_models,
    get_current_policy,
    get_telemetry_config,
    invalidate_all_credentials,
    invalidate_credential,
    list_cached_credentials,
    list_models,
    list_server_credentials,
    put_server_credential,
    send_chat,
    set_policy,
    update_auth_config,
    update_telemetry_config,
)
from luthien_proxy.credential_manager import AuthConfig, AuthMode, CachedCredential, CredentialManager
from luthien_proxy.credentials import Credential, CredentialError, CredentialType
from luthien_proxy.dependencies import require_credential_manager
from luthien_proxy.policy_manager import PolicyEnableResult

AUTH_TOKEN = "test-admin-key"


class TestGetCurrentPolicyRoute:
    """Test get_current_policy route handler."""

    @pytest.mark.asyncio
    async def test_exception_does_not_leak_details(self):
        """Test that unexpected exceptions return generic 500 without internal details."""
        mock_manager = MagicMock()
        mock_manager.get_current_policy = AsyncMock(side_effect=RuntimeError("connection to 10.0.0.5:5432 refused"))

        with pytest.raises(HTTPException) as exc_info:
            await get_current_policy(_=AUTH_TOKEN, manager=mock_manager)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal server error"
        assert "connection" not in exc_info.value.detail.lower()
        assert "10.0.0.5" not in exc_info.value.detail


class TestSetPolicyRoute:
    """Test set_policy route handler."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.validate_policy_config")
    @patch("luthien_proxy.admin.routes._import_policy_class")
    async def test_successful_set_policy(self, mock_import, mock_validate):
        """Test successful policy set returns success response."""
        mock_import.return_value = MagicMock()
        mock_validate.return_value = {}

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=True,
                policy="luthien_proxy.policies.noop_policy:NoOpPolicy",
                restart_duration_ms=50,
            )
        )

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
            enabled_by="test",
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert isinstance(result, PolicyEnableResponse)
        assert result.success is True
        assert result.policy == "luthien_proxy.policies.noop_policy:NoOpPolicy"
        assert result.restart_duration_ms == 50

        mock_manager.enable_policy.assert_called_once_with(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
            enabled_by="test",
        )

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.validate_policy_config")
    @patch("luthien_proxy.admin.routes._import_policy_class")
    async def test_set_policy_with_config(self, mock_import, mock_validate):
        """Test policy set with configuration parameters."""
        mock_import.return_value = MagicMock()
        config = {"probability_threshold": 0.8}
        mock_validate.return_value = config

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=True,
                policy="luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
                restart_duration_ms=100,
            )
        )

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
            config=config,
            enabled_by="e2e-test",
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert result.success is True
        mock_manager.enable_policy.assert_called_once_with(
            policy_class_ref="luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
            config=config,
            enabled_by="e2e-test",
        )

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.validate_policy_config")
    @patch("luthien_proxy.admin.routes._import_policy_class")
    async def test_set_policy_failure(self, mock_import, mock_validate):
        """Test policy set failure returns error response."""
        mock_import.return_value = MagicMock()
        mock_validate.return_value = {}

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=False,
                error="Module not found: nonexistent.policy",
                troubleshooting=["Check that the policy class reference is correct"],
            )
        )

        request = PolicySetRequest(
            policy_class_ref="nonexistent.policy:BadPolicy",
            config={},
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert isinstance(result, PolicyEnableResponse)
        assert result.success is False
        assert "Module not found" in (result.error or "")
        assert result.troubleshooting is not None
        assert len(result.troubleshooting) > 0

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.validate_policy_config")
    @patch("luthien_proxy.admin.routes._import_policy_class")
    async def test_set_policy_http_exception_passthrough(self, mock_import, mock_validate):
        """Test that HTTPExceptions from manager are passed through."""
        mock_import.return_value = MagicMock()
        mock_validate.return_value = {}

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Policy changes disabled")
        )

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert exc_info.value.status_code == 403
        assert "Policy changes disabled" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.validate_policy_config")
    @patch("luthien_proxy.admin.routes._import_policy_class")
    async def test_set_policy_unexpected_exception_does_not_leak_details(self, mock_import, mock_validate):
        """Test that unexpected exceptions become 500 without leaking internal details."""
        mock_import.return_value = MagicMock()
        mock_validate.return_value = {}

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(side_effect=RuntimeError("connection to 10.0.0.5:5432 refused"))

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal server error"
        assert "connection" not in exc_info.value.detail.lower()
        assert "10.0.0.5" not in exc_info.value.detail


class TestGetAvailableModels:
    """Test get_available_models function."""

    @patch("luthien_proxy.admin.routes.litellm")
    def test_returns_models_from_litellm(self, mock_litellm):
        """Test that get_available_models returns filtered Anthropic models from litellm."""
        mock_litellm.anthropic_models = [
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
            "some-other-model",  # Should be filtered out (no 'claude')
        ]

        models = get_available_models()

        # Check that only Anthropic models are returned
        assert "claude-3-5-sonnet-20241022" in models
        assert "claude-3-haiku-20240307" in models
        assert "some-other-model" not in models


class TestListModelsRoute:
    """Test list_models route handler."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_available_models")
    async def test_returns_models_list(self, mock_get_models):
        """Test that list_models returns the models in expected format."""
        mock_get_models.return_value = ["gpt-4o", "claude-3-5-sonnet-20241022"]

        result = await list_models(_=AUTH_TOKEN)

        assert "models" in result
        assert result["models"] == ["gpt-4o", "claude-3-5-sonnet-20241022"]


class _RecordingPolicy:
    """Stub Anthropic-execution policy that records hook calls.

    Honors AnthropicExecutionInterface via duck-typing — the protocol is
    runtime_checkable but we only need the two non-streaming hooks for
    these tests.
    """

    def __init__(
        self,
        *,
        request_transform=None,
        response_transform=None,
        block_response=None,
    ):
        self._request_transform = request_transform
        self._response_transform = response_transform
        self._block_response = block_response
        self.request_calls: list[dict] = []
        self.response_calls: list[dict] = []

    async def on_anthropic_request(self, request, context):
        self.request_calls.append(dict(request))
        if self._request_transform is not None:
            return self._request_transform(request)
        return request

    async def on_anthropic_response(self, response, context):
        self.response_calls.append(dict(response))
        if self._block_response is not None:
            return self._block_response
        if self._response_transform is not None:
            return self._response_transform(response)
        return response

    # Stream hooks unused by send_chat but required for protocol completeness
    async def on_anthropic_stream_event(self, event, context):
        return [event]

    async def on_anthropic_stream_complete(self, context):
        return []


def _anthropic_response(text: str, *, usage: dict | None = None) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-haiku-4-5",
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 5, "output_tokens": 7},
    }


def _make_deps(*, policy=None, anthropic_client=None, raise_on_get_policy=False):
    """Build a Dependencies-like stub adequate for send_chat's call sites."""
    deps = MagicMock()
    deps.anthropic_client = anthropic_client
    if raise_on_get_policy:
        deps.get_anthropic_policy = MagicMock(
            side_effect=HTTPException(
                status_code=500,
                detail="Current policy FooPolicy does not implement AnthropicExecutionInterface",
            )
        )
    else:
        deps.get_anthropic_policy = MagicMock(return_value=policy)
    return deps


def _make_fastapi_request(headers: dict[str, str] | None = None) -> MagicMock:
    """Build a minimal FastAPI Request stand-in.

    send_chat reads ``request.headers`` (dict-like), ``request.method``, and
    ``request.url.path`` to build the RawHttpRequest. A MagicMock with
    those attributes is sufficient.
    """
    fastapi_request = MagicMock()
    fastapi_request.headers = dict(headers or {"x-test": "1"})
    fastapi_request.method = "POST"
    fastapi_request.url = MagicMock()
    fastapi_request.url.path = "/api/admin/test/chat"
    return fastapi_request


def _make_emitter() -> MagicMock:
    """Build a recording emitter stand-in (matches EventEmitterProtocol's record(...))."""
    emitter = MagicMock()
    emitter.record = MagicMock()
    return emitter


def _make_credential_manager() -> MagicMock:
    """Build a CredentialManager stand-in.

    Tests don't exercise credential_manager methods directly; they only need
    something to thread through PolicyContext. Policies that consume the
    manager get a MagicMock — they can patch around it as needed.
    """
    return MagicMock()


def _send_chat_kwargs(
    *,
    deps,
    fastapi_request=None,
    db_pool=None,
    credential_manager=None,
    emitter=None,
):
    """Build the keyword args for send_chat with sensible defaults.

    The route handler now takes 6 dependencies; tests don't all care about
    every one. This helper centralizes the defaults.
    """
    return {
        "fastapi_request": fastapi_request or _make_fastapi_request(),
        "_": AUTH_TOKEN,
        "deps": deps,
        "db_pool": db_pool,
        "credential_manager": credential_manager or _make_credential_manager(),
        "emitter": emitter or _make_emitter(),
    }


class TestSendChatRoute:
    """Test send_chat route handler — Before/After orchestration."""

    def test_recording_policy_satisfies_anthropic_execution_interface(self):
        """Pin that the test stub structurally matches the AnthropicExecutionInterface protocol.

        AnthropicExecutionInterface is runtime_checkable; if the stub diverges
        from the protocol (e.g. a hook signature changes), this test fails
        loudly instead of letting the rest of the suite test against a stub
        that no longer represents what production policies look like.
        """
        from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicExecutionInterface

        assert isinstance(_RecordingPolicy(), AnthropicExecutionInterface)

    @pytest.mark.asyncio
    async def test_passthrough_policy_runs_one_llm_call(self):
        """Policy that doesn't transform request: single LLM call, Before == After."""
        policy = _RecordingPolicy()  # passthrough on both hooks
        before = _anthropic_response("raw LLM output")

        client = MagicMock()
        client.complete = AsyncMock(return_value=before)

        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="Hello!")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert isinstance(result, ChatResponse)
        assert result.success is True
        assert result.before_content == "raw LLM output"
        assert result.content == "raw LLM output"
        # Only one LLM call when request hook is a passthrough.
        assert client.complete.call_count == 1
        assert len(policy.request_calls) == 1
        assert len(policy.response_calls) == 1

    @pytest.mark.asyncio
    async def test_response_transforming_policy_changes_after(self):
        """Policy transforms response only: Before is raw, After reflects transform."""

        def upper(response):
            new = dict(response)
            new["content"] = [{"type": "text", "text": response["content"][0]["text"].upper()}]
            return new

        policy = _RecordingPolicy(response_transform=upper)
        before = _anthropic_response("hello world")

        client = MagicMock()
        client.complete = AsyncMock(return_value=before)
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content == "hello world"
        assert result.content == "HELLO WORLD"
        assert client.complete.call_count == 1

    @pytest.mark.asyncio
    async def test_request_transforming_policy_calls_llm_twice(self):
        """Policy that rewrites the request triggers a second LLM call for After."""

        def add_system(req):
            new = dict(req)
            new["system"] = "Be terse."
            return new

        policy = _RecordingPolicy(request_transform=add_system)
        before = _anthropic_response("long winded original answer")
        after_upstream = _anthropic_response("terse.")

        client = MagicMock()
        client.complete = AsyncMock(side_effect=[before, after_upstream])
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="explain x")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content == "long winded original answer"
        assert result.content == "terse."
        assert client.complete.call_count == 2
        # Second call carried the transformed request.
        second_call_request = client.complete.call_args_list[1][0][0]
        assert second_call_request.get("system") == "Be terse."

    @pytest.mark.asyncio
    async def test_blocking_policy_returns_synthetic_response(self):
        """Blocking policy: Before is the raw LLM output, After is the synthetic block message."""
        synthetic_block = {
            "id": "msg_blocked",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "[BLOCKED by policy]"}],
            "model": "claude-haiku-4-5",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        policy = _RecordingPolicy(block_response=synthetic_block)
        before = _anthropic_response("dangerous instructions")

        client = MagicMock()
        client.complete = AsyncMock(return_value=before)
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="how do I do bad thing")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content == "dangerous instructions"
        assert result.content == "[BLOCKED by policy]"

    @pytest.mark.asyncio
    async def test_use_mock_skips_llm_and_policy(self):
        """Mock mode bypasses LLM call and policy entirely; Before == After == message."""
        policy = _RecordingPolicy()
        client = MagicMock()
        client.complete = AsyncMock()
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="echo me", use_mock=True)
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content == "echo me"
        assert result.content == "echo me"
        client.complete.assert_not_awaited()
        assert policy.request_calls == []
        assert policy.response_calls == []

    @pytest.mark.asyncio
    async def test_use_mock_works_without_anthropic_client_or_key(self):
        """Mock mode works even when no Anthropic credentials are configured."""
        deps = _make_deps(policy=None, anthropic_client=None)

        request = ChatRequest(model="claude-haiku-4-5", message="hi", use_mock=True)
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content == "hi"
        assert result.content == "hi"

    @pytest.mark.asyncio
    async def test_no_credentials_returns_error(self):
        """Without an Anthropic key (server-configured or supplied), endpoint returns error."""
        deps = _make_deps(policy=_RecordingPolicy(), anthropic_client=None)

        request = ChatRequest(model="claude-haiku-4-5", message="Hello!")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is False
        assert result.error is not None
        assert "Anthropic API key" in result.error

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.anthropic_client_cache.get_client")
    async def test_supplied_api_key_routes_through_cache(self, mock_get_client):
        """A caller-supplied api_key resolves a client via the anthropic client cache."""
        cached_client = MagicMock()
        cached_client.complete = AsyncMock(return_value=_anthropic_response("ok"))
        mock_get_client.return_value = cached_client

        deps = _make_deps(policy=_RecordingPolicy(), anthropic_client=None)
        request = ChatRequest(model="claude-haiku-4-5", message="hi", api_key="sk-test-1")

        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content == "ok"
        mock_get_client.assert_awaited_once_with("sk-test-1", auth_type="api_key")

    @pytest.mark.asyncio
    async def test_llm_failure_is_reported(self):
        """LLM call failure surfaces as an error response (no content, no Before)."""
        policy = _RecordingPolicy()
        client = MagicMock()
        client.complete = AsyncMock(side_effect=RuntimeError("anthropic 500"))
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is False
        assert result.before_content is None
        assert result.content is None
        assert result.error is not None
        # Policy hooks should not have run if the LLM call failed.
        assert policy.request_calls == []
        assert policy.response_calls == []

    @pytest.mark.asyncio
    async def test_policy_request_hook_failure_surfaces_with_before(self):
        """If on_anthropic_request raises, Before is preserved and an error is returned."""

        def boom(_req):
            raise RuntimeError("policy request hook crashed")

        policy = _RecordingPolicy(request_transform=boom)
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("raw"))
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is False
        assert result.before_content == "raw"
        assert result.content is None
        assert result.error is not None
        assert "request hook" in result.error.lower()

    @pytest.mark.asyncio
    async def test_policy_response_hook_failure_surfaces_with_before(self):
        """If on_anthropic_response raises, Before is preserved and an error is returned."""

        def boom(_resp):
            raise RuntimeError("policy response hook crashed")

        policy = _RecordingPolicy(response_transform=boom)
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("raw"))
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is False
        assert result.before_content == "raw"
        assert result.content is None
        assert result.error is not None
        assert "response hook" in result.error.lower()

    @pytest.mark.asyncio
    async def test_active_policy_not_anthropic_raises_500(self):
        """If the active policy doesn't implement AnthropicExecutionInterface, 500 is raised."""
        client = MagicMock()
        client.complete = AsyncMock()
        deps = _make_deps(policy=None, anthropic_client=client, raise_on_get_policy=True)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        with pytest.raises(HTTPException) as exc_info:
            await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_no_text_blocks_returns_none_content(self):
        """Tool-use-only response (no text blocks) returns None content — preview can't render it."""
        tool_use_response = {
            "id": "msg_tool",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "calc", "input": {"x": 1}},
            ],
            "model": "claude-haiku-4-5",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }
        policy = _RecordingPolicy()
        client = MagicMock()
        client.complete = AsyncMock(return_value=tool_use_response)
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="use a tool")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_content is None
        assert result.content is None

    @pytest.mark.asyncio
    async def test_does_not_make_http_call_to_v1_messages(self):
        """Regression guard: send_chat must not import or use httpx for /v1/messages roundtrip.

        Architectural principle — policies decide; clients (including this admin
        path) do not. The previous design routed test traffic through the
        gateway HTTP boundary, which let a client opt into a non-default
        response shape via a header. The current design orchestrates LLM and
        policy as two in-process steps. This test pins that.
        """
        import luthien_proxy.admin.routes as admin_routes

        assert not hasattr(admin_routes, "httpx"), (
            "admin.routes must not import httpx — the test endpoint orchestrates "
            "LLM + policy in-process and never crosses the /v1/messages HTTP boundary."
        )

    def test_chat_request_api_key_defaults_to_none(self):
        """ChatRequest.api_key defaults to None when not provided."""
        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!")
        assert request.api_key is None

    def test_chat_request_accepts_api_key(self):
        """ChatRequest accepts api_key parameter."""
        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", api_key="sk-test")
        assert request.api_key == "sk-test"

    def test_chat_response_includes_before_content_field(self):
        """ChatResponse includes the new before_content field."""
        resp = ChatResponse(success=True, content="after", before_content="before")
        assert resp.content == "after"
        assert resp.before_content == "before"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.anthropic_client_cache.get_client")
    async def test_full_policy_context_is_threaded_through_hooks(self, mock_get_client):
        """The PolicyContext passed to hooks carries emitter, credential_manager, raw_http_request, session_id."""

        captured_contexts: list = []

        class _ContextCapturingPolicy(_RecordingPolicy):
            async def on_anthropic_request(self, request, context):
                captured_contexts.append(context)
                return await super().on_anthropic_request(request, context)

            async def on_anthropic_response(self, response, context):
                captured_contexts.append(context)
                return await super().on_anthropic_response(response, context)

        policy = _ContextCapturingPolicy()
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("ok"))
        mock_get_client.return_value = client
        deps = _make_deps(policy=policy, anthropic_client=None)
        emitter = _make_emitter()
        cred_mgr = _make_credential_manager()
        fastapi_request = _make_fastapi_request({"x-session-id": "abc", "anthropic-beta": "prompt-caching-2024-07-31"})

        request = ChatRequest(model="claude-haiku-4-5", message="hi", api_key="sk-test-1")
        result = await send_chat(
            body=request,
            **_send_chat_kwargs(
                deps=deps,
                fastapi_request=fastapi_request,
                emitter=emitter,
                credential_manager=cred_mgr,
            ),
        )
        assert result.success is True

        # Both hooks saw the same ctx; ctx is fully populated.
        assert len(captured_contexts) == 2
        ctx = captured_contexts[0]
        assert ctx is captured_contexts[1]
        # transaction_id is per-test (uuid prefix).
        assert ctx.transaction_id.startswith("admin-test-")
        # session_id marks it as test traffic.
        assert ctx.session_id is not None
        assert ctx.session_id.startswith("admin-test-session-")
        # Emitter and credential manager are the wired dependencies.
        assert ctx.emitter is emitter
        # credential_manager flows through; access via the property does not raise.
        assert ctx._credential_manager is cred_mgr  # noqa: SLF001 — testing wiring
        # user_credential reflects body.api_key (passthrough-style API key credential).
        assert ctx.user_credential is not None
        assert ctx.user_credential.value == "sk-test-1"
        assert ctx.user_credential.credential_type == CredentialType.API_KEY
        # raw_http_request carries the inbound headers from the admin caller.
        assert ctx.raw_http_request is not None
        assert ctx.raw_http_request.headers.get("anthropic-beta") == "prompt-caching-2024-07-31"
        assert ctx.raw_http_request.path == "/api/admin/test/chat"

    @pytest.mark.asyncio
    async def test_user_credential_is_none_when_no_api_key_supplied(self):
        """Without body.api_key, user_credential is None — matches client-key-mode semantics."""

        captured: list = []

        class _Cap(_RecordingPolicy):
            async def on_anthropic_request(self, request, context):
                captured.append(context)
                return await super().on_anthropic_request(request, context)

        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("x"))
        # Server has its own anthropic client configured (client-key mode).
        deps = _make_deps(policy=_Cap(), anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")  # no api_key
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))
        assert result.success is True
        assert captured[0].user_credential is None

    @pytest.mark.asyncio
    async def test_policy_cache_factory_is_set_when_db_pool_present(self):
        """When db_pool is present, ctx.has_policy_cache is True (factory was wired)."""

        captured: list = []

        class _Cap(_RecordingPolicy):
            async def on_anthropic_request(self, request, context):
                captured.append(context)
                return await super().on_anthropic_request(request, context)

        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("x"))
        deps = _make_deps(policy=_Cap(), anthropic_client=client)
        # db_pool is a stand-in — PolicyCache won't actually run, the factory is just installed.
        db_pool = MagicMock()

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps, db_pool=db_pool))
        assert result.success is True
        assert captured[0].has_policy_cache is True

    @pytest.mark.asyncio
    async def test_policy_cache_factory_is_none_without_db_pool(self):
        """Without a db_pool, ctx.has_policy_cache is False — same as dockerless dev without DB."""

        captured: list = []

        class _Cap(_RecordingPolicy):
            async def on_anthropic_request(self, request, context):
                captured.append(context)
                return await super().on_anthropic_request(request, context)

        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("x"))
        deps = _make_deps(policy=_Cap(), anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps, db_pool=None))
        assert result.success is True
        assert captured[0].has_policy_cache is False

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.anthropic_client_cache.get_client")
    async def test_simple_llm_policy_runs_end_to_end_via_test_endpoint(self, mock_get_client):
        """End-to-end: a SimpleLLMPolicy runs through the test endpoint with a real PolicyContext.

        Mocks at the Anthropic-client and judge boundaries only — the policy's
        own machinery (block descriptors, credential resolution via
        credential_manager.resolve, judge invocation, response rebuilding)
        runs unmodified. This pins that the test endpoint can preview the
        most operationally-interesting class of policies (LLM judges) and
        not just trivial response transformers.
        """
        from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
        from luthien_proxy.policies.simple_llm_utils import (
            JudgeAction,
            ReplacementBlock,
            SimpleLLMJudgeConfig,
        )

        config = SimpleLLMJudgeConfig(
            instructions="Replace any mention of 'cat' with 'dog'.",
            on_error="pass",
            auth_provider="user_credentials",
        )
        policy = SimpleLLMPolicy(config=config)

        # Anthropic-client returns a text block; judge returns a "replace" action.
        before = _anthropic_response("I love cats.")
        client = MagicMock()
        client.complete = AsyncMock(return_value=before)
        mock_get_client.return_value = client

        # credential_manager.resolve must return a Credential (the user_credential
        # from the test ctx, since auth_provider='user_credentials').
        cred_mgr = MagicMock()
        cred_mgr.resolve = AsyncMock(
            return_value=Credential(
                value="sk-test-1",
                credential_type=CredentialType.API_KEY,
                platform="anthropic",
            )
        )

        deps = _make_deps(policy=policy, anthropic_client=None)
        request = ChatRequest(model="claude-haiku-4-5", message="tell me about cats", api_key="sk-test-1")

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            new=AsyncMock(
                return_value=JudgeAction(
                    action="replace",
                    blocks=(ReplacementBlock(type="text", text="I love dogs."),),
                )
            ),
        ):
            result = await send_chat(
                body=request,
                **_send_chat_kwargs(deps=deps, credential_manager=cred_mgr),
            )

        assert result.success is True
        assert result.before_content == "I love cats."
        assert result.content == "I love dogs."
        # credential_manager.resolve was actually called by the policy — proves
        # the full PolicyContext (with credential_manager wired) threaded
        # through the hooks.
        cred_mgr.resolve.assert_awaited()

    @pytest.mark.asyncio
    async def test_in_place_mutating_request_hook_still_triggers_second_llm_call(self):
        """Regression: pre-hook snapshot detects mutation even when the hook returns the same ref.

        The earlier signature-comparison code read the live request dict on
        both sides of the hook call. A policy that mutates in place and
        returns the same reference would defeat that — both sides see the
        post-mutation state, look equal, and the optimizer wrongly skipped
        the second LLM call. This test pins that we now snapshot before the
        hook runs.
        """

        def mutate_in_place_return_same_ref(req):
            # Mutate the input dict and return the same reference.
            req["system"] = "mutated-by-policy"
            return req

        policy = _RecordingPolicy(request_transform=mutate_in_place_return_same_ref)
        before = _anthropic_response("original")
        after_upstream = _anthropic_response("mutated-context-answer")

        client = MagicMock()
        client.complete = AsyncMock(side_effect=[before, after_upstream])
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        # Two LLM calls — the snapshot-vs-transformed comparison correctly
        # detects the mutation despite the same-reference return.
        assert client.complete.call_count == 2
        assert result.before_content == "original"
        assert result.content == "mutated-context-answer"
        # Second call carried the mutated request.
        second_call_request = client.complete.call_args_list[1][0][0]
        assert second_call_request.get("system") == "mutated-by-policy"

    @pytest.mark.asyncio
    async def test_anthropic_beta_header_forwarded_to_upstream(self):
        """anthropic-beta on the inbound admin request flows into both LLM calls.

        Mirrors the gateway's beta-header forwarding so beta features (prompt
        caching with scope, etc.) behave identically in the preview and in
        production.
        """

        def add_system(req):
            new = dict(req)
            new["system"] = "Be terse."
            return new

        # Two-LLM-call path so we can assert the header on both calls.
        policy = _RecordingPolicy(request_transform=add_system)
        client = MagicMock()
        client.complete = AsyncMock(
            side_effect=[_anthropic_response("a"), _anthropic_response("b")],
        )
        deps = _make_deps(policy=policy, anthropic_client=client)
        fastapi_request = _make_fastapi_request({"anthropic-beta": "prompt-caching-2024-07-31"})

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(
            body=request,
            **_send_chat_kwargs(deps=deps, fastapi_request=fastapi_request),
        )
        assert result.success is True

        # Both LLM calls carried the beta header.
        assert client.complete.call_count == 2
        for call in client.complete.call_args_list:
            assert call.kwargs.get("extra_headers") == {"anthropic-beta": "prompt-caching-2024-07-31"}

    @pytest.mark.asyncio
    async def test_no_extra_headers_forwarded_when_no_beta(self):
        """Without anthropic-beta on the inbound request, extra_headers is None."""
        policy = _RecordingPolicy()
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("ok"))
        deps = _make_deps(policy=policy, anthropic_client=client)
        # Default _make_fastapi_request has no anthropic-beta header.

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))
        assert result.success is True

        assert client.complete.call_count == 1
        assert client.complete.call_args.kwargs.get("extra_headers") is None

    @pytest.mark.asyncio
    async def test_before_usage_equals_usage_when_single_llm_call(self):
        """No request transformation: before_usage and usage are populated identically."""
        policy = _RecordingPolicy()
        usage = {"input_tokens": 11, "output_tokens": 22}
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("ok", usage=usage))
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_usage == usage
        assert result.usage == usage

    @pytest.mark.asyncio
    async def test_before_usage_distinct_when_two_llm_calls(self):
        """Request-transforming policy: before_usage and usage carry independent counts."""

        def add_system(req):
            new = dict(req)
            new["system"] = "Be terse."
            return new

        policy = _RecordingPolicy(request_transform=add_system)
        before_usage = {"input_tokens": 100, "output_tokens": 50}
        after_usage = {"input_tokens": 110, "output_tokens": 5}
        client = MagicMock()
        client.complete = AsyncMock(
            side_effect=[
                _anthropic_response("long answer", usage=before_usage),
                _anthropic_response("terse.", usage=after_usage),
            ],
        )
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert result.before_usage == before_usage
        assert result.usage == after_usage

    @pytest.mark.asyncio
    async def test_before_usage_surfaced_on_policy_failure_paths(self):
        """When a policy hook fails, before_usage is preserved alongside before_content."""

        def boom(_resp):
            raise RuntimeError("policy response hook crashed")

        policy = _RecordingPolicy(response_transform=boom)
        before_usage = {"input_tokens": 7, "output_tokens": 9}
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("raw", usage=before_usage))
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is False
        assert result.before_content == "raw"
        assert result.before_usage == before_usage
        # On error, usage falls back to before_usage too — operators still see cost.
        assert result.usage == before_usage

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.anthropic_client_cache.get_client")
    async def test_supplied_api_key_cache_failure_returns_user_facing_error(self, mock_get_client):
        """If the AnthropicClient cache raises, send_chat returns a clean error string."""
        mock_get_client.side_effect = RuntimeError("boom")
        deps = _make_deps(policy=_RecordingPolicy(), anthropic_client=None)

        request = ChatRequest(model="claude-haiku-4-5", message="hi", api_key="sk-bad")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is False
        assert result.error is not None
        assert "Failed to initialize Anthropic client" in result.error

    @pytest.mark.asyncio
    async def test_raw_http_request_body_aliases_anthropic_request(self):
        """Pin the production-parity contract: ``ctx.raw_http_request.body is request``.

        In the gateway pipeline (pipeline/anthropic_processor.py) the parsed
        JSON body is reused as both ``RawHttpRequest.body`` and the typed
        ``AnthropicRequest`` — same dict identity. The admin test path must
        match: a policy that mutates ``request`` in ``on_anthropic_request``
        and then reads ``ctx.raw_http_request.body`` must see the mutation.
        Without this parity, the Before/After preview would lie about what
        production does for any policy that writes through ``request`` and
        reads through ``raw_http_request.body``.

        Two assertions: (1) direct identity — the request the hook receives
        IS the body on raw_http_request; (2) mutation propagation — a
        top-level write on ``request`` is visible via ``raw_http_request.body``
        without going through the AnthropicRequest path again.
        """

        observations: dict[str, object] = {}

        class _IdentityCheckingPolicy(_RecordingPolicy):
            async def on_anthropic_request(self, request, context):
                # Record identity at hook-entry time.
                observations["request_is_body"] = request is context.raw_http_request.body
                # Mutate the request and verify the mutation is visible via
                # raw_http_request.body — the operator-visible contract.
                request["system"] = "mutated-by-test"
                observations["body_sees_mutation"] = context.raw_http_request.body.get("system") == "mutated-by-test"
                return request

        policy = _IdentityCheckingPolicy()
        client = MagicMock()
        client.complete = AsyncMock(return_value=_anthropic_response("ok"))
        deps = _make_deps(policy=policy, anthropic_client=client)

        request = ChatRequest(model="claude-haiku-4-5", message="hi")
        result = await send_chat(body=request, **_send_chat_kwargs(deps=deps))

        assert result.success is True
        assert observations["request_is_body"] is True, (
            "raw_http_request.body must alias the AnthropicRequest the hook receives — "
            "production parity at pipeline/anthropic_processor.py."
        )
        assert observations["body_sees_mutation"] is True, (
            "Top-level mutations on the AnthropicRequest must be visible through "
            "raw_http_request.body. If not, the Before/After preview lies about production."
        )


class TestAuthConfigUpdateRequestValidation:
    """Test Pydantic validation on AuthConfigUpdateRequest."""

    def test_rejects_zero_valid_ttl(self):
        with pytest.raises(ValidationError):
            AuthConfigUpdateRequest(valid_cache_ttl_seconds=0)

    def test_rejects_negative_invalid_ttl(self):
        with pytest.raises(ValidationError):
            AuthConfigUpdateRequest(invalid_cache_ttl_seconds=-1)

    def test_accepts_positive_ttl(self):
        req = AuthConfigUpdateRequest(valid_cache_ttl_seconds=60, invalid_cache_ttl_seconds=30)
        assert req.valid_cache_ttl_seconds == 60
        assert req.invalid_cache_ttl_seconds == 30

    def test_accepts_none_ttl(self):
        req = AuthConfigUpdateRequest()
        assert req.valid_cache_ttl_seconds is None
        assert req.invalid_cache_ttl_seconds is None


class TestGetAuthConfig:
    """Test get_auth_config route handler."""

    def _make_manager(self, **overrides):
        config = AuthConfig(
            auth_mode=AuthMode.PASSTHROUGH,
            validate_credentials=True,
            valid_cache_ttl_seconds=3600,
            invalid_cache_ttl_seconds=300,
            updated_at="2025-01-01 00:00:00",
            updated_by="admin",
            **overrides,
        )
        manager = MagicMock()
        manager.config = config
        return manager

    @pytest.mark.asyncio
    async def test_returns_current_config(self):
        manager = self._make_manager()
        result = await get_auth_config(_=AUTH_TOKEN, credential_manager=manager)
        assert isinstance(result, AuthConfigResponse)
        assert result.auth_mode == "passthrough"
        assert result.validate_credentials is True
        assert result.valid_cache_ttl_seconds == 3600
        assert result.updated_by == "admin"


class TestUpdateAuthConfig:
    """Test update_auth_config route handler."""

    @pytest.mark.asyncio
    async def test_updates_config(self):
        updated = AuthConfig(
            auth_mode=AuthMode.BOTH,
            validate_credentials=False,
            valid_cache_ttl_seconds=7200,
            invalid_cache_ttl_seconds=600,
            updated_at="2025-06-01 00:00:00",
            updated_by="admin-api",
        )
        manager = MagicMock()
        manager.update_config = AsyncMock(return_value=updated)

        body = AuthConfigUpdateRequest(auth_mode="both", validate_credentials=False)
        result = await update_auth_config(body=body, _=AUTH_TOKEN, credential_manager=manager)

        assert isinstance(result, AuthConfigResponse)
        assert result.auth_mode == "both"
        assert result.validate_credentials is False
        manager.update_config.assert_called_once_with(
            auth_mode="both",
            validate_credentials=False,
            valid_cache_ttl_seconds=None,
            invalid_cache_ttl_seconds=None,
            updated_by="admin-api",
        )

    @pytest.mark.asyncio
    async def test_invalid_auth_mode_returns_400(self):
        manager = MagicMock()
        body = AuthConfigUpdateRequest(auth_mode="invalid_mode")
        with pytest.raises(HTTPException) as exc_info:
            await update_auth_config(body=body, _=AUTH_TOKEN, credential_manager=manager)
        assert exc_info.value.status_code == 400
        assert "Invalid auth_mode" in exc_info.value.detail


class TestListCachedCredentials:
    """Test list_cached_credentials route handler."""

    @pytest.mark.asyncio
    async def test_returns_cached_list(self):
        manager = MagicMock()
        manager.list_cached = AsyncMock(
            return_value=[
                CachedCredential(key_hash="abc123", valid=True, validated_at=1000.0, last_used_at=2000.0),
                CachedCredential(key_hash="def456", valid=False, validated_at=1500.0, last_used_at=1500.0),
            ]
        )

        result = await list_cached_credentials(_=AUTH_TOKEN, credential_manager=manager)
        assert isinstance(result, CachedCredentialsListResponse)
        assert result.count == 2
        assert result.credentials[0].key_hash == "abc123"
        assert result.credentials[0].valid is True
        assert result.credentials[1].valid is False

    @pytest.mark.asyncio
    async def test_empty_cache(self):
        manager = MagicMock()
        manager.list_cached = AsyncMock(return_value=[])
        result = await list_cached_credentials(_=AUTH_TOKEN, credential_manager=manager)
        assert result.count == 0
        assert result.credentials == []


class TestInvalidateCredential:
    """Test invalidate_credential route handler."""

    @pytest.mark.asyncio
    async def test_invalidates_existing(self):
        manager = MagicMock()
        manager.invalidate_credential = AsyncMock(return_value=True)
        result = await invalidate_credential(key_hash="abc123", _=AUTH_TOKEN, credential_manager=manager)
        assert result["success"] is True
        manager.invalidate_credential.assert_called_once_with("abc123")

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        manager = MagicMock()
        manager.invalidate_credential = AsyncMock(return_value=False)
        with pytest.raises(HTTPException) as exc_info:
            await invalidate_credential(key_hash="missing", _=AUTH_TOKEN, credential_manager=manager)
        assert exc_info.value.status_code == 404


class TestInvalidateAllCredentials:
    """Test invalidate_all_credentials route handler."""

    @pytest.mark.asyncio
    async def test_invalidates_all(self):
        manager = MagicMock()
        manager.invalidate_all = AsyncMock(return_value=5)
        result = await invalidate_all_credentials(_=AUTH_TOKEN, credential_manager=manager)
        assert result["success"] is True
        assert result["count"] == 5

    @pytest.mark.asyncio
    async def test_empty_cache(self):
        manager = MagicMock()
        manager.invalidate_all = AsyncMock(return_value=0)
        result = await invalidate_all_credentials(_=AUTH_TOKEN, credential_manager=manager)
        assert result["count"] == 0


class TestRequireCredentialManager:
    """Test require_credential_manager dependency."""

    @pytest.mark.asyncio
    async def test_returns_manager_when_available(self):
        manager = MagicMock(spec=CredentialManager)
        result = await require_credential_manager(credential_manager=manager)
        assert result is manager

    @pytest.mark.asyncio
    async def test_raises_503_when_none(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_credential_manager(credential_manager=None)
        assert exc_info.value.status_code == 503


class TestConfigToResponse:
    """Test _config_to_response helper."""

    def test_converts_config_to_response(self):
        config = AuthConfig(
            auth_mode=AuthMode.PASSTHROUGH,
            validate_credentials=True,
            valid_cache_ttl_seconds=3600,
            invalid_cache_ttl_seconds=300,
            updated_at="2025-01-01 00:00:00",
            updated_by="admin",
        )
        result = _config_to_response(config)
        assert isinstance(result, AuthConfigResponse)
        assert result.auth_mode == "passthrough"
        assert result.validate_credentials is True
        assert result.valid_cache_ttl_seconds == 3600
        assert result.updated_by == "admin"


class TestGetTelemetryConfig:
    """Test get_telemetry_config route handler."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.resolve_telemetry_config")
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_returns_config(self, mock_settings, mock_resolve):
        from luthien_proxy.usage_telemetry.config import TelemetryConfig

        mock_settings.return_value = MagicMock(usage_telemetry=None)
        mock_resolve.return_value = TelemetryConfig(enabled=True, deployment_id="test-uuid", user_configured=True)

        result = await get_telemetry_config(_=AUTH_TOKEN, db_pool=MagicMock())

        assert result.enabled is True
        assert result.deployment_id == "test-uuid"
        assert result.env_override is False
        assert result.user_configured is True

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.resolve_telemetry_config")
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_env_override_flag(self, mock_settings, mock_resolve):
        from luthien_proxy.usage_telemetry.config import TelemetryConfig

        mock_settings.return_value = MagicMock(usage_telemetry=False)
        mock_resolve.return_value = TelemetryConfig(enabled=False, deployment_id="test-uuid")

        result = await get_telemetry_config(_=AUTH_TOKEN, db_pool=MagicMock())

        assert result.env_override is True
        assert result.enabled is False


class TestUpdateTelemetryConfig:
    """Test update_telemetry_config route handler."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_updates_db(self, mock_settings):
        mock_settings.return_value = MagicMock(usage_telemetry=None)
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.get_pool = AsyncMock(return_value=mock_conn)

        body = TelemetryConfigUpdateRequest(enabled=False)
        result = await update_telemetry_config(body=body, _=AUTH_TOKEN, db_pool=mock_pool)

        assert result["success"] is True
        assert result["enabled"] is False
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_rejects_when_env_override_set(self, mock_settings):
        mock_settings.return_value = MagicMock(usage_telemetry=True)

        body = TelemetryConfigUpdateRequest(enabled=False)
        with pytest.raises(HTTPException) as exc_info:
            await update_telemetry_config(body=body, _=AUTH_TOKEN, db_pool=MagicMock())

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_rejects_when_no_db(self, mock_settings):
        mock_settings.return_value = MagicMock(usage_telemetry=None)

        body = TelemetryConfigUpdateRequest(enabled=True)
        with pytest.raises(HTTPException) as exc_info:
            await update_telemetry_config(body=body, _=AUTH_TOKEN, db_pool=None)

        assert exc_info.value.status_code == 503


class TestPutServerCredential:
    """Test put_server_credential route handler."""

    @pytest.mark.asyncio
    async def test_successful_put(self):
        """Test successful credential creation returns success response."""
        mock_cm = MagicMock()
        mock_cm.put_server_credential = AsyncMock()

        request = ServerCredentialRequest(
            name="judge-key",
            value="sk-test123",
            credential_type="api_key",
            platform="anthropic",
        )

        result = await put_server_credential(body=request, _=AUTH_TOKEN, credential_manager=mock_cm)

        assert result["success"] is True
        assert result["name"] == "judge-key"
        mock_cm.put_server_credential.assert_called_once()
        call_args = mock_cm.put_server_credential.call_args
        assert call_args[0][0] == "judge-key"

    @pytest.mark.asyncio
    async def test_invalid_credential_type(self):
        """Test invalid credential_type raises HTTPException with 400."""
        mock_cm = MagicMock()

        request = ServerCredentialRequest(
            name="test-key",
            value="x",
            credential_type="bad_type",
            platform="anthropic",
        )

        with pytest.raises(HTTPException) as exc_info:
            await put_server_credential(body=request, _=AUTH_TOKEN, credential_manager=mock_cm)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_credential_error_returns_503(self):
        """Test CredentialError is converted to 503."""
        mock_cm = MagicMock()
        mock_cm.put_server_credential = AsyncMock(side_effect=CredentialError("No credential store configured"))

        request = ServerCredentialRequest(
            name="judge-key",
            value="sk-test123",
            credential_type="api_key",
            platform="anthropic",
        )

        with pytest.raises(HTTPException) as exc_info:
            await put_server_credential(body=request, _=AUTH_TOKEN, credential_manager=mock_cm)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Server credential operation failed"

    @pytest.mark.asyncio
    async def test_name_validation(self):
        """Test name pattern validation rejects invalid names."""
        with pytest.raises(ValidationError):
            ServerCredentialRequest(
                name="invalid name!!",
                value="x",
                credential_type="api_key",
                platform="anthropic",
            )


class TestListServerCredentials:
    """Test list_server_credentials route handler."""

    @pytest.mark.asyncio
    async def test_successful_list(self):
        """Test listing credentials returns names and count."""
        mock_cm = MagicMock()
        mock_cm.list_server_credentials = AsyncMock(return_value=["key-a", "key-b"])

        result = await list_server_credentials(_=AUTH_TOKEN, credential_manager=mock_cm)

        assert result["credentials"] == ["key-a", "key-b"]
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """Test listing empty credentials."""
        mock_cm = MagicMock()
        mock_cm.list_server_credentials = AsyncMock(return_value=[])

        result = await list_server_credentials(_=AUTH_TOKEN, credential_manager=mock_cm)

        assert result["credentials"] == []
        assert result["count"] == 0


class TestDeleteServerCredential:
    """Test delete_server_credential route handler."""

    @pytest.mark.asyncio
    async def test_successful_delete(self):
        """Test successful deletion returns success response."""
        mock_cm = MagicMock()
        mock_cm.delete_server_credential = AsyncMock(return_value=True)

        result = await delete_server_credential(name="judge-key", _=AUTH_TOKEN, credential_manager=mock_cm)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Test deleting non-existent credential raises 404."""
        mock_cm = MagicMock()
        mock_cm.delete_server_credential = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            await delete_server_credential(name="nonexistent", _=AUTH_TOKEN, credential_manager=mock_cm)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_credential_error_returns_503(self):
        """Test CredentialError is converted to 503."""
        mock_cm = MagicMock()
        mock_cm.delete_server_credential = AsyncMock(side_effect=CredentialError("Store error"))

        with pytest.raises(HTTPException) as exc_info:
            await delete_server_credential(name="judge-key", _=AUTH_TOKEN, credential_manager=mock_cm)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Server credential operation failed"


class TestConfigRoutes:
    """Admin config dashboard routes: GET/PUT/DELETE /api/admin/config."""

    @pytest.mark.asyncio
    async def test_get_config_dashboard_returns_view(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, get_config_dashboard  # noqa: F401

        mock_registry = MagicMock()
        mock_registry.dashboard_view.return_value = [{"name": "gateway_port", "value": 8000, "sensitive": False}]
        result = await get_config_dashboard(_=AUTH_TOKEN, registry=mock_registry)
        assert result == {"config": [{"name": "gateway_port", "value": 8000, "sensitive": False}]}

    @pytest.mark.asyncio
    async def test_put_config_unknown_field_404(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value

        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await set_config_value(
                body=ConfigSetRequest(value=True),
                key="nonexistent",
                subject=AUTH_TOKEN,
                registry=mock_registry,
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_put_config_not_db_settable_400(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value

        mock_meta = MagicMock()
        mock_meta.db_settable = False
        mock_meta.sensitive = False
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        with pytest.raises(HTTPException) as exc_info:
            await set_config_value(
                body=ConfigSetRequest(value=8000),
                key="gateway_port",
                subject=AUTH_TOKEN,
                registry=mock_registry,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_put_config_cli_override_409(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value
        from luthien_proxy.config_registry import ConfigOverriddenError, ConfigSource

        mock_meta = MagicMock()
        mock_meta.db_settable = True
        mock_meta.sensitive = False
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        mock_registry.set_db_value = AsyncMock(side_effect=ConfigOverriddenError("dogfood_mode", ConfigSource.CLI))
        with pytest.raises(HTTPException) as exc_info:
            await set_config_value(
                body=ConfigSetRequest(value=True),
                key="dogfood_mode",
                subject=AUTH_TOKEN,
                registry=mock_registry,
            )
        assert exc_info.value.status_code == 409
        assert "cli" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_put_config_coerce_failure_422(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value

        mock_meta = MagicMock()
        mock_meta.db_settable = True
        mock_meta.sensitive = False
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        mock_registry.set_db_value = AsyncMock(
            side_effect=ValueError("Invalid boolean value for 'dogfood_mode': 'ture'")
        )
        with pytest.raises(HTTPException) as exc_info:
            await set_config_value(
                body=ConfigSetRequest(value="ture"),
                key="dogfood_mode",
                subject=AUTH_TOKEN,
                registry=mock_registry,
            )
        assert exc_info.value.status_code == 422
        assert "Invalid boolean" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_put_config_sensitive_response_masked(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value
        from luthien_proxy.config_registry import ConfigSource, ResolvedValue

        mock_meta = MagicMock()
        mock_meta.db_settable = True
        mock_meta.sensitive = True
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        mock_registry.set_db_value = AsyncMock(return_value=ResolvedValue(value="secret123", source=ConfigSource.DB))
        result = await set_config_value(
            body=ConfigSetRequest(value="secret123"),
            key="sentry_dsn",
            subject=AUTH_TOKEN,
            registry=mock_registry,
        )
        assert result["value"] == "***"
        assert "secret123" not in str(result)

    @pytest.mark.asyncio
    async def test_put_config_success_passes_subject_fingerprint(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value
        from luthien_proxy.config_registry import ConfigSource, ResolvedValue

        mock_meta = MagicMock()
        mock_meta.db_settable = True
        mock_meta.sensitive = False
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        mock_registry.set_db_value = AsyncMock(return_value=ResolvedValue(value=True, source=ConfigSource.DB))
        await set_config_value(
            body=ConfigSetRequest(value=True),
            key="dogfood_mode",
            subject="some-raw-admin-key",
            registry=mock_registry,
        )
        passed_subject = mock_registry.set_db_value.call_args.kwargs["updated_by"]
        # Never the raw token, always a fingerprint.
        assert passed_subject != "some-raw-admin-key"
        assert passed_subject.startswith("admin:")

    @pytest.mark.asyncio
    async def test_put_config_localhost_bypass_subject(self):
        from luthien_proxy.admin.routes import ConfigSetRequest, set_config_value
        from luthien_proxy.config_registry import ConfigSource, ResolvedValue

        mock_meta = MagicMock()
        mock_meta.db_settable = True
        mock_meta.sensitive = False
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        mock_registry.set_db_value = AsyncMock(return_value=ResolvedValue(value=True, source=ConfigSource.DB))
        await set_config_value(
            body=ConfigSetRequest(value=True),
            key="dogfood_mode",
            subject="localhost-bypass",
            registry=mock_registry,
        )
        assert mock_registry.set_db_value.call_args.kwargs["updated_by"] == "admin-localhost"

    @pytest.mark.asyncio
    async def test_delete_config_cli_override_409(self):
        from luthien_proxy.admin.routes import delete_config_value
        from luthien_proxy.config_registry import ConfigOverriddenError, ConfigSource

        mock_meta = MagicMock()
        mock_meta.db_settable = True
        mock_meta.sensitive = False
        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = mock_meta
        mock_registry.delete_db_value = AsyncMock(side_effect=ConfigOverriddenError("dogfood_mode", ConfigSource.ENV))
        with pytest.raises(HTTPException) as exc_info:
            await delete_config_value(key="dogfood_mode", _=AUTH_TOKEN, registry=mock_registry)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_config_unknown_field_404(self):
        from luthien_proxy.admin.routes import delete_config_value

        mock_registry = MagicMock()
        mock_registry.get_field_meta.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await delete_config_value(key="nonexistent", _=AUTH_TOKEN, registry=mock_registry)
        assert exc_info.value.status_code == 404


class TestInferenceProviderRoutes:
    """Test inference-provider admin route handlers."""

    @pytest.mark.asyncio
    async def test_put_inference_provider_success(self):
        from luthien_proxy.admin.routes import (
            InferenceProviderRequest,
            put_inference_provider,
        )

        mock_registry = MagicMock()
        mock_registry.put = AsyncMock()

        request = InferenceProviderRequest(
            name="judge-one",
            backend_type="claude_code",
            credential_name="judge-cred",
            default_model="claude-sonnet-4-6",
            config={"timeout_seconds": 30},
        )

        result = await put_inference_provider(body=request, _=AUTH_TOKEN, registry=mock_registry)

        assert result == {"success": True, "name": "judge-one"}
        mock_registry.put.assert_awaited_once()
        record = mock_registry.put.await_args[0][0]
        assert record.name == "judge-one"
        assert record.backend_type == "claude_code"
        assert record.credential_name == "judge-cred"
        assert record.default_model == "claude-sonnet-4-6"
        assert record.config == {"timeout_seconds": 30}

    @pytest.mark.asyncio
    async def test_put_inference_provider_unknown_backend_returns_400(self):
        from luthien_proxy.admin.routes import (
            InferenceProviderRequest,
            put_inference_provider,
        )
        from luthien_proxy.inference.registry import UnknownBackendTypeError

        mock_registry = MagicMock()
        mock_registry.put = AsyncMock(side_effect=UnknownBackendTypeError("no such backend"))

        request = InferenceProviderRequest(
            name="p",
            backend_type="nope",
            credential_name=None,
            default_model="m",
            config={},
        )
        with pytest.raises(HTTPException) as exc:
            await put_inference_provider(body=request, _=AUTH_TOKEN, registry=mock_registry)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_put_inference_provider_registry_error_returns_503(self):
        from luthien_proxy.admin.routes import (
            InferenceProviderRequest,
            put_inference_provider,
        )
        from luthien_proxy.inference.registry import InferenceRegistryError

        mock_registry = MagicMock()
        mock_registry.put = AsyncMock(side_effect=InferenceRegistryError("db down"))

        request = InferenceProviderRequest(
            name="p",
            backend_type="claude_code",
            credential_name=None,
            default_model="m",
            config={},
        )
        with pytest.raises(HTTPException) as exc:
            await put_inference_provider(body=request, _=AUTH_TOKEN, registry=mock_registry)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_put_inference_provider_name_validation(self):
        from luthien_proxy.admin.routes import InferenceProviderRequest

        with pytest.raises(ValidationError):
            InferenceProviderRequest(
                name="invalid name!!",
                backend_type="claude_code",
                credential_name=None,
                default_model="m",
                config={},
            )

    @pytest.mark.asyncio
    async def test_list_inference_providers_returns_records(self):
        from luthien_proxy.admin.routes import list_inference_providers
        from luthien_proxy.inference.registry import ProviderRecord

        mock_registry = MagicMock()
        mock_registry.list = AsyncMock(
            return_value=[
                ProviderRecord(
                    name="a",
                    backend_type="claude_code",
                    credential_name="cred",
                    default_model="m",
                    config={"k": "v"},
                    created_at="2026-01-01",
                    updated_at="2026-01-02",
                ),
            ]
        )
        mock_registry.known_backend_types = MagicMock(return_value=("claude_code", "direct_api"))

        result = await list_inference_providers(_=AUTH_TOKEN, registry=mock_registry)
        assert result.count == 1
        assert result.providers[0].name == "a"
        assert result.providers[0].credential_name == "cred"
        assert result.providers[0].config == {"k": "v"}
        assert result.providers[0].known_backend is True
        assert result.known_backend_types == ["claude_code", "direct_api"]

    @pytest.mark.asyncio
    async def test_list_inference_providers_flags_unknown_backend(self):
        from luthien_proxy.admin.routes import list_inference_providers
        from luthien_proxy.inference.registry import ProviderRecord

        mock_registry = MagicMock()
        mock_registry.list = AsyncMock(
            return_value=[
                ProviderRecord(
                    name="legacy",
                    backend_type="retired_backend",
                    credential_name=None,
                    default_model="m",
                    config={},
                    created_at=None,
                    updated_at=None,
                ),
            ]
        )
        mock_registry.known_backend_types = MagicMock(return_value=("claude_code", "direct_api"))

        result = await list_inference_providers(_=AUTH_TOKEN, registry=mock_registry)
        assert result.providers[0].known_backend is False

    @pytest.mark.asyncio
    async def test_put_inference_provider_rejects_oversized_config(self):
        from luthien_proxy.admin.routes import InferenceProviderRequest
        from luthien_proxy.inference.registry import MAX_CONFIG_JSON_BYTES

        # 65 KiB — 1 KiB over the ceiling.
        oversized = {"x": "a" * (MAX_CONFIG_JSON_BYTES + 1024)}
        with pytest.raises(ValidationError):
            InferenceProviderRequest(
                name="p",
                backend_type="claude_code",
                credential_name=None,
                default_model="m",
                config=oversized,
            )

    @pytest.mark.asyncio
    async def test_delete_inference_provider_success(self):
        from luthien_proxy.admin.routes import delete_inference_provider

        mock_registry = MagicMock()
        mock_registry.delete = AsyncMock(return_value=True)

        result = await delete_inference_provider(name="p", _=AUTH_TOKEN, registry=mock_registry)
        assert result == {"success": True, "name": "p"}

    @pytest.mark.asyncio
    async def test_delete_inference_provider_not_found_returns_404(self):
        from luthien_proxy.admin.routes import delete_inference_provider

        mock_registry = MagicMock()
        mock_registry.delete = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc:
            await delete_inference_provider(name="p", _=AUTH_TOKEN, registry=mock_registry)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_inference_provider_registry_error_returns_503(self):
        from luthien_proxy.admin.routes import delete_inference_provider
        from luthien_proxy.inference.registry import InferenceRegistryError

        mock_registry = MagicMock()
        mock_registry.delete = AsyncMock(side_effect=InferenceRegistryError("db down"))

        with pytest.raises(HTTPException) as exc:
            await delete_inference_provider(name="p", _=AUTH_TOKEN, registry=mock_registry)
        assert exc.value.status_code == 503


class TestWebhookStatsRoute:
    """Test /api/admin/webhook/stats route handler."""

    @pytest.mark.asyncio
    async def test_returns_disabled_shape_when_sender_missing(self):
        import os

        from luthien_proxy.admin.routes import webhook_stats

        result = await webhook_stats(_=AUTH_TOKEN, webhook_sender=None)
        assert result.enabled is False
        assert result.safe_url == ""
        assert result.pending_depth == 0
        assert result.dropped_count == 0
        assert result.gave_up_count == 0
        assert result.permanent_failure_count == 0
        assert result.max_pending_tasks == 0
        assert result.started_at == ""
        assert result.worker_pid == os.getpid()

    @pytest.mark.asyncio
    async def test_returns_sender_counters(self):
        from datetime import UTC, datetime

        from luthien_proxy.admin.routes import webhook_stats

        started = datetime(2026, 5, 9, 11, 0, 0, tzinfo=UTC)
        sender = MagicMock()
        sender.enabled = True
        sender.safe_url = "https://hooks.example.com/..."
        sender.pending_depth = 7
        sender.dropped_count = 42
        sender.gave_up_count = 5
        sender.permanent_failure_count = 3
        sender.max_pending_tasks = 1000
        sender.started_at = started

        result = await webhook_stats(_=AUTH_TOKEN, webhook_sender=sender)
        assert result.enabled is True
        assert result.safe_url == "https://hooks.example.com/..."
        assert result.pending_depth == 7
        assert result.dropped_count == 42
        assert result.gave_up_count == 5
        assert result.permanent_failure_count == 3
        assert result.max_pending_tasks == 1000
        assert result.started_at == started.isoformat()
        assert result.worker_pid > 0
