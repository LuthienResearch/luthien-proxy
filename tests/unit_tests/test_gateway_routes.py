"""Unit tests for gateway routes - auth modes and client resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import DEFAULT_TEST_MODEL
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


class TestAnthropicClientWithAuthToken:
    """Test AnthropicClient.with_auth_token() method."""

    def test_creates_new_instance(self):
        original = AnthropicClient(api_key="original-key")
        new_client = original.with_auth_token("oauth-token")
        assert new_client is not original
        assert isinstance(new_client, AnthropicClient)

    def test_preserves_base_url(self):
        original = AnthropicClient(api_key="original-key", base_url="https://custom.api.com")
        new_client = original.with_auth_token("oauth-token")
        assert new_client._base_url == "https://custom.api.com"


class TestGatewayAuthAndClientResolution:
    """Test auth modes and Anthropic client resolution via resolve_anthropic_client."""

    @pytest.fixture
    def mock_app(self):
        """Create a minimal FastAPI app with gateway routes for testing."""
        from luthien_proxy.dependencies import Dependencies
        from luthien_proxy.gateway_routes import router
        from luthien_proxy.policy_core import AnthropicExecutionInterface

        app = FastAPI()
        app.include_router(router)

        mock_policy_manager = MagicMock()
        mock_policy = MagicMock()
        mock_policy.__class__.__name__ = "TestPolicy"
        mock_policy_manager.current_policy = mock_policy

        mock_anthropic_client = MagicMock(spec=AnthropicClient)
        mock_anthropic_client._base_url = None

        mock_credential_manager = MagicMock(spec=CredentialManager)
        mock_credential_manager.config = AuthConfig(
            auth_mode=AuthMode.PROXY_KEY,
            validate_credentials=True,
            valid_cache_ttl_seconds=3600,
            invalid_cache_ttl_seconds=300,
        )
        mock_credential_manager.validate_credential = AsyncMock(return_value=True)
        mock_credential_manager._redis = AsyncMock()
        mock_credential_manager._redis.setex = AsyncMock()

        mock_anthropic_policy = MagicMock(spec=AnthropicExecutionInterface)

        deps = MagicMock(spec=Dependencies)
        deps.api_key = "test-proxy-key"
        deps.anthropic_client = mock_anthropic_client
        deps.credential_manager = mock_credential_manager
        deps.policy = mock_policy
        deps.emitter = MagicMock()
        deps.db_pool = None
        deps.enable_request_logging = False
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
                    "model": DEFAULT_TEST_MODEL,
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
                "model": DEFAULT_TEST_MODEL,
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
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer some-anthropic-token"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_called_once_with("some-anthropic-token", is_bearer=True)

    def test_passthrough_mode_rejects_invalid(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH
        credential_manager.validate_credential = AsyncMock(return_value=False)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": DEFAULT_TEST_MODEL,
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
                    "model": DEFAULT_TEST_MODEL,
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
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer some-anthropic-token"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_called_once_with("some-anthropic-token", is_bearer=True)

    def test_missing_key_returns_401(self, mock_app):
        app, _, _, _ = mock_app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
        )
        assert response.status_code == 401

    def test_x_anthropic_api_key_header_takes_precedence(self, mock_app):
        app, mock_anthropic_client, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.anthropic_client_cache") as mock_cache,
        ):
            mock_cache.get_client = AsyncMock(return_value=MagicMock())
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={
                    "Authorization": "Bearer test-proxy-key",
                    "x-anthropic-api-key": "sk-ant-client-key-123",
                },
            )
            mock_cache.get_client.assert_called_once_with("sk-ant-client-key-123", auth_type="api_key", base_url=None)

    def test_empty_x_anthropic_api_key_returns_401(self, mock_app):
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={
                "Authorization": "Bearer test-proxy-key",
                "x-anthropic-api-key": "",
            },
        )
        assert response.status_code == 401

    def test_both_mode_no_validation_forwards_passthrough(self, mock_app):
        """In BOTH mode with validate_credentials=False, non-proxy tokens pass through."""
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.BOTH
        credential_manager.config.validate_credentials = False

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.anthropic_client_cache") as mock_cache,
        ):
            mock_cache.get_client = AsyncMock(return_value=MagicMock())
            mock_process.return_value = MagicMock()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer some-anthropic-token"},
            )
            assert response.status_code == 200
            credential_manager.validate_credential.assert_not_called()
            mock_cache.get_client.assert_called_once_with("some-anthropic-token", auth_type="auth_token", base_url=None)

    def test_passthrough_bearer_creates_auth_token_client(self, mock_app):
        """In passthrough mode, a Bearer credential creates an auth_token client."""
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.anthropic_client_cache") as mock_cache,
        ):
            mock_cache.get_client = AsyncMock(return_value=MagicMock())
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer my-anthropic-token"},
            )
            mock_cache.get_client.assert_called_once_with("my-anthropic-token", auth_type="auth_token", base_url=None)

    def test_passthrough_bearer_with_api_key_prefix_creates_api_key_client(self, mock_app):
        """In passthrough mode, API keys via Bearer header create api_key client.
        API keys are always sent via api_key regardless of transport header."""
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.anthropic_client_cache") as mock_cache,
        ):
            mock_cache.get_client = AsyncMock(return_value=MagicMock())
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer sk-ant-api03-test-key"},
            )
            mock_cache.get_client.assert_called_once_with("sk-ant-api03-test-key", auth_type="api_key", base_url=None)

    def test_passthrough_api_key_creates_api_key_client(self, mock_app):
        """In passthrough mode, an x-api-key credential creates an api_key client."""
        app, _, credential_manager, _ = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.anthropic_client_cache") as mock_cache,
        ):
            mock_cache.get_client = AsyncMock(return_value=MagicMock())
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"x-api-key": "sk-ant-my-key"},
            )
            mock_cache.get_client.assert_called_once_with("sk-ant-my-key", auth_type="api_key", base_url=None)

    def test_no_anthropic_client_returns_500_for_proxy_key(self, mock_app):
        """Proxy key auth with no ANTHROPIC_API_KEY configured returns 500."""
        app, _, credential_manager, deps = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY
        deps.anthropic_client = None

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/v1/messages",
            json={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
            },
            headers={"Authorization": "Bearer test-proxy-key"},
        )
        assert response.status_code == 500

    def test_passthrough_bearer_records_oauth_credential_type(self, mock_app):
        """In passthrough mode with bearer token, deps.last_credential_info updated with oauth type."""
        app, _, credential_manager, deps = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH
        deps.last_credential_info = {}

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.AnthropicClient") as MockClient,
        ):
            MockClient.return_value = MagicMock()
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer my-oauth-token"},
            )
            assert deps.last_credential_info["type"] == "oauth"
            assert "timestamp" in deps.last_credential_info

    def test_proxy_key_mode_does_not_record_credential_type(self, mock_app):
        """In proxy_key mode, last_credential_info is NOT updated (recording skipped)."""
        app, _, credential_manager, deps = mock_app
        credential_manager.config.auth_mode = AuthMode.PROXY_KEY
        deps.last_credential_info = {}

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.AnthropicClient") as MockClient,
        ):
            MockClient.return_value = MagicMock()
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-proxy-key"},
            )
            assert deps.last_credential_info == {}

    def test_x_anthropic_api_key_records_client_api_key_type(self, mock_app):
        """When x-anthropic-api-key header provided, deps.last_credential_info updated with client_api_key type."""
        app, _, credential_manager, deps = mock_app
        credential_manager.config.auth_mode = AuthMode.PASSTHROUGH
        deps.last_credential_info = {}

        with (
            patch("luthien_proxy.gateway_routes.process_anthropic_request", new_callable=AsyncMock) as mock_process,
            patch("luthien_proxy.gateway_routes.AnthropicClient") as MockClient,
        ):
            MockClient.return_value = MagicMock()
            mock_process.return_value = MagicMock()
            client = TestClient(app)
            response = client.post(
                "/v1/messages",
                json={
                    "model": DEFAULT_TEST_MODEL,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 10,
                },
                headers={
                    "Authorization": "Bearer test-proxy-key",
                    "x-anthropic-api-key": "sk-ant-client-key-123",
                },
            )
            assert response.status_code == 200
            assert deps.last_credential_info["type"] == "client_api_key"
            assert "timestamp" in deps.last_credential_info
