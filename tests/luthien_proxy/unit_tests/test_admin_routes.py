# ABOUTME: Unit tests for admin route handlers
# ABOUTME: Tests HTTP layer for policy management endpoints

"""Tests for admin route handlers.

These tests focus on the HTTP layer - ensuring routes properly:
- Handle dependency injection
- Convert service exceptions to appropriate HTTP status codes
- Return correct response models
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
from luthien_proxy.credentials import CredentialError
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


class TestSendChatRoute:
    """Test send_chat route handler."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_successful_chat_request(self, mock_client_class, mock_get_settings):
        """Test successful test chat request."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "msg_123",
            "content": [{"type": "text", "text": "Hello from the LLM!"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!")

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is True
        assert result.content == "Hello from the LLM!"
        assert result.model == "claude-3-haiku-20240307"
        assert result.usage is not None
        assert result.usage["input_tokens"] == 10

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8000/v1/messages"
        assert call_args[1]["json"]["model"] == "claude-3-haiku-20240307"
        assert call_args[1]["json"]["messages"][0]["content"] == "Hello!"
        assert call_args[1]["headers"]["x-api-key"] == "test-proxy-key"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_missing_proxy_api_key_and_no_custom(self, mock_get_settings):
        """Test send_chat returns error when no API key is available."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = None
        mock_get_settings.return_value = mock_settings

        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert "No API key available" in result.error
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_custom_key_works_without_proxy_key(self, mock_client_class, mock_get_settings):
        """Custom api_key works even when PROXY_API_KEY is not configured."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = None
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "msg_123",
            "content": [{"type": "text", "text": "OK"}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", api_key="custom-key")

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert result.success is True
        call_args = mock_client.post.call_args
        assert call_args[1]["headers"]["x-api-key"] == "custom-key"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_proxy_error_response(self, mock_client_class, mock_get_settings):
        """Test send_chat handles proxy error responses."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_response.json.return_value = {"detail": "Invalid model specified"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="invalid-model", message="Hello!")

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert "400" in result.error
        assert "Invalid model specified" in result.error

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_timeout_exception(self, mock_client_class, mock_get_settings):
        """Test send_chat handles timeout exceptions."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_get_settings.return_value = mock_settings

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        mock_settings.gateway_port = 8000
        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert "timed out" in result.error
        assert "120s" in result.error

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_unexpected_exception_does_not_leak_details(self, mock_client_class, mock_get_settings):
        """Test send_chat returns generic error without leaking internal details."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_settings.gateway_port = 8000
        mock_settings.verbose_client_errors = False
        mock_get_settings.return_value = mock_settings

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("connection to 10.0.0.5:5432 refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert result.error == "An unexpected error occurred"
        assert "connection" not in result.error.lower()
        assert "10.0.0.5" not in result.error

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_uses_localhost_with_gateway_port(self, mock_client_class, mock_get_settings):
        """send_chat always calls http://localhost:{gateway_port}, not the external request URL.

        This ensures the test chat endpoint works both on the host and inside Docker,
        where the external port mapping is not reachable from within the container.
        """
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_settings.gateway_port = 9999
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_123", "content": [{"type": "text", "text": "OK"}]}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Test")

        await send_chat(body=request, _=AUTH_TOKEN)

        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert url == "http://localhost:9999/v1/messages"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_real_llm_call_by_default(self, mock_client_class, mock_get_settings):
        """Default use_mock=False does not include mock_response (real LLM call attempted)."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_123", "content": [{"type": "text", "text": "real"}]}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!")  # use_mock defaults to False

        await send_chat(body=request, _=AUTH_TOKEN)

        call_args = mock_client.post.call_args
        assert "mock_response" not in call_args[1]["json"]

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_mock_response_sent_when_use_mock_true(self, mock_get_settings):
        """When use_mock=True, function returns directly without calling gateway."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", use_mock=True)

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is True
        assert result.content == "Hello!"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_mock_mode_works_without_any_api_key(self, mock_get_settings):
        """Mock mode bypasses API key validation entirely."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = None
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        request = ChatRequest(model="claude-3-haiku-20240307", message="test echo", use_mock=True)

        result = await send_chat(body=request, _=AUTH_TOKEN)

        assert result.success is True
        assert result.content == "test echo"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_custom_api_key_overrides_proxy_key(self, mock_client_class, mock_get_settings):
        """When api_key is provided, it's used instead of the server's proxy key."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "server-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_123", "content": [{"type": "text", "text": "OK"}]}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", api_key="custom-key-123")

        await send_chat(body=request, _=AUTH_TOKEN)

        call_args = mock_client.post.call_args
        assert call_args[1]["headers"]["x-api-key"] == "custom-key-123"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_none_api_key_uses_proxy_key(self, mock_client_class, mock_get_settings):
        """When api_key is None (default), the server's proxy key is used."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "server-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_123", "content": [{"type": "text", "text": "OK"}]}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!")

        await send_chat(body=request, _=AUTH_TOKEN)

        call_args = mock_client.post.call_args
        assert call_args[1]["headers"]["x-api-key"] == "server-proxy-key"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_empty_api_key_uses_proxy_key(self, mock_client_class, mock_get_settings):
        """An empty string api_key falls back to the server's proxy key."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "server-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "msg_123", "content": [{"type": "text", "text": "OK"}]}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", api_key="")

        await send_chat(body=request, _=AUTH_TOKEN)

        call_args = mock_client.post.call_args
        assert call_args[1]["headers"]["x-api-key"] == "server-proxy-key"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_whitespace_api_key_uses_proxy_key(self, mock_client_class, mock_get_settings):
        """A whitespace-only api_key falls back to the server's proxy key."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "server-proxy-key"
        mock_settings.gateway_port = 8000
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "msg_123",
            "content": [{"type": "text", "text": "OK"}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", api_key="   ")

        await send_chat(body=request, _=AUTH_TOKEN)

        call_args = mock_client.post.call_args
        assert call_args[1]["headers"]["x-api-key"] == "server-proxy-key"

    def test_chat_request_api_key_defaults_to_none(self):
        """ChatRequest.api_key defaults to None when not provided."""
        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!")
        assert request.api_key is None

    def test_chat_request_accepts_api_key(self):
        """ChatRequest accepts api_key parameter."""
        request = ChatRequest(model="claude-3-haiku-20240307", message="Hello!", api_key="sk-test")
        assert request.api_key == "sk-test"


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
