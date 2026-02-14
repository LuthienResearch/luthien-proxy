"""Unit tests for gateway routes - Anthropic passthrough authentication."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.llm.anthropic_client import AnthropicClient


class TestAnthropicPassthroughAuth:
    """Test x-anthropic-api-key header handling in /v1/messages."""

    @pytest.fixture
    def mock_app(self):
        """Create a minimal FastAPI app with gateway routes for testing."""
        from fastapi import FastAPI

        from luthien_proxy.dependencies import Dependencies
        from luthien_proxy.gateway_routes import router

        app = FastAPI()
        app.include_router(router)

        mock_policy_manager = MagicMock()
        mock_policy = MagicMock()
        mock_policy.__class__.__name__ = "TestPolicy"
        # Make mock satisfy both interface checks
        from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface

        mock_policy_manager.current_policy = mock_policy

        mock_anthropic_client = MagicMock(spec=AnthropicClient)
        mock_anthropic_client.with_api_key = MagicMock(return_value=MagicMock(spec=AnthropicClient))

        deps = MagicMock(spec=Dependencies)
        deps.api_key = "test-proxy-key"
        deps.anthropic_client = mock_anthropic_client
        deps.get_anthropic_client.return_value = mock_anthropic_client
        deps.policy = mock_policy
        deps.emitter = MagicMock()

        # Make get_anthropic_policy return a proper mock
        mock_anthropic_policy = MagicMock(spec=AnthropicPolicyInterface)
        deps.get_anthropic_policy.return_value = mock_anthropic_policy

        app.state.dependencies = deps
        return app, mock_anthropic_client, deps

    @pytest.mark.asyncio
    async def test_no_client_key_uses_proxy_client(self, mock_app):
        """Request without x-anthropic-api-key uses the proxy's client."""
        app, mock_anthropic_client, deps = mock_app

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()

            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-proxy-key"},
            )

            # with_api_key should NOT have been called
            mock_anthropic_client.with_api_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_key_creates_new_client(self, mock_app):
        """Request with x-anthropic-api-key creates a new client with that key."""
        app, mock_anthropic_client, deps = mock_app

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()

            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={
                    "Authorization": "Bearer test-proxy-key",
                    "x-anthropic-api-key": "sk-ant-client-key-123",
                },
            )

            mock_anthropic_client.with_api_key.assert_called_once_with("sk-ant-client-key-123")
            # The overridden client should be passed to process_anthropic_request
            call_kwargs = mock_process.call_args.kwargs
            assert call_kwargs["anthropic_client"] == mock_anthropic_client.with_api_key.return_value

    @pytest.mark.asyncio
    async def test_empty_client_key_returns_401(self, mock_app):
        """Request with empty x-anthropic-api-key returns 401."""
        app, _, _ = mock_app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={
                "Authorization": "Bearer test-proxy-key",
                "x-anthropic-api-key": "",
            },
        )

        assert response.status_code == 401
        assert "empty" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_client_key_returns_401(self, mock_app):
        """Request with whitespace-only x-anthropic-api-key returns 401."""
        app, _, _ = mock_app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={
                "Authorization": "Bearer test-proxy-key",
                "x-anthropic-api-key": "   ",
            },
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_proxy_auth_still_required(self, mock_app):
        """Proxy API key is still required even with client Anthropic key."""
        app, _, _ = mock_app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={
                "x-anthropic-api-key": "sk-ant-client-key-123",
                # No proxy auth header
            },
        )

        assert response.status_code == 401


class TestAnthropicClientWithApiKey:
    """Test AnthropicClient.with_api_key() method."""

    def test_with_api_key_creates_new_client(self):
        """with_api_key returns a new AnthropicClient instance."""
        original = AnthropicClient(api_key="original-key")
        new_client = original.with_api_key("new-key")

        assert new_client is not original
        assert isinstance(new_client, AnthropicClient)

    def test_with_api_key_preserves_base_url(self):
        """with_api_key preserves the original base_url."""
        original = AnthropicClient(api_key="original-key", base_url="https://custom.api.com")
        new_client = original.with_api_key("new-key")

        assert new_client._base_url == "https://custom.api.com"

    def test_with_api_key_no_base_url(self):
        """with_api_key works when no base_url was set."""
        original = AnthropicClient(api_key="original-key")
        new_client = original.with_api_key("new-key")

        assert new_client._base_url is None
