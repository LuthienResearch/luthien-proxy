"""Unit tests for gateway routes - auth modes and passthrough authentication."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.credential_manager import AuthConfig, AuthMode, CredentialManager
from luthien_proxy.llm.anthropic_client import AnthropicClient


class TestAnthropicClientWithApiKey:
    """Test AnthropicClient.with_api_key() method."""

    def test_creates_new_instance(self):
        original = AnthropicClient(api_key="original-key")
        new_client = original.with_api_key("new-key")
        assert new_client is not original
        assert isinstance(new_client, AnthropicClient)

    def test_preserves_base_url(self):
        original = AnthropicClient(api_key="original-key", base_url="https://custom.api.com")
        new_client = original.with_api_key("new-key")
        assert new_client._base_url == "https://custom.api.com"

    def test_no_base_url(self):
        original = AnthropicClient(api_key="original-key")
        new_client = original.with_api_key("new-key")
        assert new_client._base_url is None


class TestVerifyTokenAuthModes:
    """Test verify_token with different auth modes."""

    @pytest.fixture
    def mock_app(self):
        """Create a minimal FastAPI app with gateway routes for testing."""
        from luthien_proxy.dependencies import Dependencies
        from luthien_proxy.gateway_routes import router
        from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface

        app = FastAPI()
        app.include_router(router)

        mock_policy_manager = MagicMock()
        mock_policy = MagicMock()
        mock_policy.__class__.__name__ = "TestPolicy"
        mock_policy_manager.current_policy = mock_policy

        mock_anthropic_client = MagicMock(spec=AnthropicClient)
        mock_anthropic_client.with_api_key = MagicMock(return_value=MagicMock(spec=AnthropicClient))

        mock_credential_manager = MagicMock(spec=CredentialManager)
        mock_credential_manager.config = AuthConfig(
            auth_mode=AuthMode.PROXY_KEY,
            validate_credentials=True,
            valid_cache_ttl_seconds=3600,
            invalid_cache_ttl_seconds=300,
        )
        mock_credential_manager.validate_credential = AsyncMock(return_value=True)

        mock_anthropic_policy = MagicMock(spec=AnthropicPolicyInterface)

        deps = MagicMock(spec=Dependencies)
        deps.api_key = "test-proxy-key"
        deps.anthropic_client = mock_anthropic_client
        deps.get_anthropic_client.return_value = mock_anthropic_client
        deps.credential_manager = mock_credential_manager
        deps.policy = mock_policy
        deps.emitter = MagicMock()
        deps.get_anthropic_policy.return_value = mock_anthropic_policy

        app.state.dependencies = deps
        return app, mock_anthropic_client, mock_credential_manager, deps

    def test_proxy_key_mode_accepts_correct_key(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-proxy-key"},
            )
            assert response.status_code == 200

    def test_proxy_key_mode_rejects_wrong_key(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_passthrough_mode_validates_credential(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer some-anthropic-token"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_called_once_with("some-anthropic-token")

    def test_passthrough_mode_rejects_invalid(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH
        credential_manager.validate_credential = AsyncMock(return_value=False)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={"Authorization": "Bearer bad-token"},
        )
        assert response.status_code == 401

    def test_both_mode_accepts_proxy_key(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.BOTH

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-proxy-key"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_not_called()

    def test_both_mode_falls_through_to_passthrough(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.BOTH

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer some-anthropic-token"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_called_once_with("some-anthropic-token")

    def test_missing_key_returns_401(self, mock_app):
        app, _, _, _ = mock_app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
        )
        assert response.status_code == 401

    def test_x_anthropic_api_key_header_takes_precedence(self, mock_app):
        app, mock_anthropic_client, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY

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

    def test_empty_x_anthropic_api_key_returns_401(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY

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

    def test_both_mode_no_validation_sets_passthrough(self, mock_app):
        """In BOTH mode with validate_credentials=False, non-proxy tokens pass through."""
        app, mock_anthropic_client, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.BOTH
        credential_manager.config.validate_credentials = False

        with patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer some-anthropic-token"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_not_called()
            mock_anthropic_client.with_api_key.assert_called_once_with("some-anthropic-token")

    def test_passthrough_key_used_for_upstream(self, mock_app):
        """In passthrough mode, the auth credential is used as the upstream API key."""
        app, mock_anthropic_client, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH

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
                headers={"Authorization": "Bearer my-anthropic-token"},
            )
            mock_anthropic_client.with_api_key.assert_called_once_with("my-anthropic-token")
