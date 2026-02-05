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

from luthien_proxy.admin.routes import (
    ChatRequest,
    ChatResponse,
    PolicyEnableResponse,
    PolicySetRequest,
    get_available_models,
    list_models,
    send_chat,
    set_policy,
)
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
        config = {"judge_model": "claude-haiku-4-5", "block_threshold": 0.8}
        mock_validate.return_value = config

        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=True,
                policy="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
                restart_duration_ms=100,
            )
        )

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            config=config,
            enabled_by="e2e-test",
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert result.success is True
        mock_manager.enable_policy.assert_called_once_with(
            policy_class_ref="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
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

    @patch("luthien_proxy.admin.routes.litellm")
    def test_handles_missing_attributes(self, mock_litellm):
        """Test that get_available_models handles missing litellm attributes."""
        # Remove the attributes to simulate them not existing
        if hasattr(mock_litellm, "open_ai_chat_completion_models"):
            delattr(mock_litellm, "open_ai_chat_completion_models")
        if hasattr(mock_litellm, "anthropic_models"):
            delattr(mock_litellm, "anthropic_models")

        models = get_available_models()

        # Should return empty list when attributes don't exist
        assert models == []


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
