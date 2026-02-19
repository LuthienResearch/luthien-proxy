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
    _config_to_response,
    get_auth_config,
    get_available_models,
    invalidate_all_credentials,
    invalidate_credential,
    list_cached_credentials,
    list_models,
    send_chat,
    set_policy,
    update_auth_config,
)
from luthien_proxy.credential_manager import AuthConfig, AuthMode, CachedCredential, CredentialManager
from luthien_proxy.dependencies import require_credential_manager
from luthien_proxy.policy_manager import PolicyEnableResult

AUTH_TOKEN = "test-admin-key"


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
    async def test_set_policy_unexpected_exception(self, mock_import, mock_validate):
        """Test that unexpected exceptions become 500 errors."""
        mock_import.return_value = MagicMock()
        mock_validate.return_value = {}

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(side_effect=RuntimeError("Unexpected database error"))

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert exc_info.value.status_code == 500
        assert "Unexpected database error" in exc_info.value.detail


class TestGetAvailableModels:
    """Test get_available_models function."""

    @patch("luthien_proxy.admin.routes.litellm")
    def test_returns_models_from_litellm(self, mock_litellm):
        """Test that get_available_models returns filtered models from litellm."""
        mock_litellm.open_ai_chat_completion_models = [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-3.5-turbo",
            "ft:gpt-4o-mini-custom",  # Should be filtered out (fine-tuned)
            "gpt-4o-audio-preview",  # Should be filtered out (audio)
            "gpt-4o-realtime-preview",  # Should be filtered out (realtime)
        ]
        mock_litellm.anthropic_models = [
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
            "some-other-model",  # Should be filtered out (no 'claude')
        ]

        models = get_available_models()

        # Check that OpenAI models are filtered correctly
        assert "gpt-4o" in models
        assert "gpt-4o-mini" in models
        assert "gpt-3.5-turbo" in models
        assert "ft:gpt-4o-mini-custom" not in models
        assert "gpt-4o-audio-preview" not in models
        assert "gpt-4o-realtime-preview" not in models

        # Check that Anthropic models are filtered correctly
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
        # Mock settings
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_get_settings.return_value = mock_settings

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello from the LLM!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        # Mock the async client
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        # Create mock request
        mock_request = MagicMock()
        mock_request.base_url = "http://localhost:8000/"
        mock_request.headers = {}

        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is True
        assert result.content == "Hello from the LLM!"
        assert result.model == "gpt-4o"
        assert result.usage is not None
        assert result.usage["prompt_tokens"] == 10

        # Verify the HTTP call was made correctly
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8000/v1/chat/completions"
        assert call_args[1]["json"]["model"] == "gpt-4o"
        assert call_args[1]["json"]["messages"][0]["content"] == "Hello!"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-proxy-key"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    async def test_missing_proxy_api_key(self, mock_get_settings):
        """Test send_chat returns error when PROXY_API_KEY is not configured."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = None
        mock_get_settings.return_value = mock_settings

        mock_request = MagicMock()
        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert "PROXY_API_KEY not configured" in result.error
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_proxy_error_response(self, mock_client_class, mock_get_settings):
        """Test send_chat handles proxy error responses."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_get_settings.return_value = mock_settings

        # Mock error response
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_response.json.return_value = {"detail": "Invalid model specified"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        mock_request = MagicMock()
        mock_request.base_url = "http://localhost:8000/"
        mock_request.headers = {}
        request = ChatRequest(model="invalid-model", message="Hello!")

        result = await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

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

        # Mock timeout
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        mock_request = MagicMock()
        mock_request.base_url = "http://localhost:8000/"
        mock_request.headers = {}
        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert "timed out" in result.error
        assert "120s" in result.error

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_unexpected_exception(self, mock_client_class, mock_get_settings):
        """Test send_chat handles unexpected exceptions."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_get_settings.return_value = mock_settings

        # Mock unexpected exception
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("Unexpected error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        mock_request = MagicMock()
        mock_request.base_url = "http://localhost:8000/"
        mock_request.headers = {}
        request = ChatRequest(model="gpt-4o", message="Hello!")

        result = await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

        assert isinstance(result, ChatResponse)
        assert result.success is False
        assert "Unexpected error" in result.error

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_respects_x_forwarded_proto_header(self, mock_client_class, mock_get_settings):
        """Test that send_chat uses HTTPS when X-Forwarded-Proto indicates it.

        Behind reverse proxies (Railway, Heroku, etc.), the internal request uses
        HTTP but the proxy handles HTTPS. The X-Forwarded-Proto header tells us
        the original protocol, which we must use to avoid redirect issues.
        """
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        mock_request = MagicMock()
        mock_request.base_url = "http://internal-host:8000/"
        mock_request.headers = {"x-forwarded-proto": "https"}
        request = ChatRequest(model="gpt-4o", message="Test")

        await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

        # Verify the URL was upgraded to HTTPS
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert url.startswith("https://"), (
            f"URL should start with https:// when X-Forwarded-Proto is 'https', got: {url}"
        )
        assert "internal-host:8000" in url

    @pytest.mark.asyncio
    @patch("luthien_proxy.admin.routes.get_settings")
    @patch("luthien_proxy.admin.routes.httpx.AsyncClient")
    async def test_preserves_http_when_no_forwarded_proto(self, mock_client_class, mock_get_settings):
        """Test that HTTP is preserved when there's no X-Forwarded-Proto header."""
        mock_settings = MagicMock()
        mock_settings.proxy_api_key = "test-proxy-key"
        mock_get_settings.return_value = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        mock_request = MagicMock()
        mock_request.base_url = "http://localhost:8000/"
        mock_request.headers = {}  # No X-Forwarded-Proto
        request = ChatRequest(model="gpt-4o", message="Test")

        await send_chat(body=request, request=mock_request, _=AUTH_TOKEN)

        # Verify the URL remains HTTP
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert url.startswith("http://"), f"URL should remain http:// locally, got: {url}"


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
