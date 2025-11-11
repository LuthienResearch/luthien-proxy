"""Integration tests for request size limits middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.v2.main import create_app
from luthien_proxy.v2.policies.noop_policy import NoOpPolicy

pytestmark = pytest.mark.integration


@pytest.fixture
def app_with_small_limit():
    """Create app with small request size limit for testing."""
    import os
    from unittest.mock import patch

    # Override MAX_REQUEST_SIZE for this test
    with patch.dict(os.environ, {"MAX_REQUEST_SIZE": "1024"}):  # 1KB limit
        app = create_app(
            api_key="test-key",
            database_url="",  # No DB needed for integration test
            redis_url="",  # No Redis needed for integration test
            policy=NoOpPolicy(),
        )
        yield app


@pytest.fixture
def app_with_default_limit():
    """Create app with default request size limit."""
    app = create_app(
        api_key="test-key",
        database_url="",
        redis_url="",
        policy=NoOpPolicy(),
    )
    return app


class TestRequestSizeLimits:
    """Integration tests for request size validation."""

    def test_normal_request_accepted(self, app_with_small_limit):
        """Should accept normal-sized requests."""
        with TestClient(app_with_small_limit) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 10,
                    "stream": False,
                },
                headers={"Authorization": "Bearer test-key"},
            )

            # Should NOT get 413 (might get other errors like missing API keys,
            # but that's fine - we're just testing the middleware doesn't block it)
            assert response.status_code != 413

    def test_oversized_request_rejected_openai(self, app_with_small_limit):
        """Should reject oversized requests to OpenAI endpoint."""
        with TestClient(app_with_small_limit) as client:
            # Create a large payload that exceeds 1KB
            large_content = "x" * 2000
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": large_content}],
                    "max_tokens": 10,
                    "stream": False,
                },
                headers={"Authorization": "Bearer test-key"},
            )

            assert response.status_code == 413
            error_data = response.json()
            assert "payload too large" in error_data["error"].lower()
            assert "max_size_bytes" in error_data
            assert error_data["max_size_bytes"] == 1024

    def test_oversized_request_rejected_anthropic(self, app_with_small_limit):
        """Should reject oversized requests to Anthropic endpoint."""
        with TestClient(app_with_small_limit) as client:
            # Create a large payload that exceeds 1KB
            large_content = "x" * 2000
            response = client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "messages": [{"role": "user", "content": large_content}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-key"},
            )

            assert response.status_code == 413
            error_data = response.json()
            assert "payload too large" in error_data["error"].lower()

    def test_error_response_includes_size_info(self, app_with_small_limit):
        """Should return detailed error response with size information."""
        with TestClient(app_with_small_limit) as client:
            large_content = "x" * 2000
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": large_content}],
                },
                headers={"Authorization": "Bearer test-key"},
            )

            assert response.status_code == 413
            error_data = response.json()

            # Verify error structure
            assert "error" in error_data
            assert "detail" in error_data
            assert "max_size_bytes" in error_data
            assert "received_size_bytes" in error_data

            # Verify values
            assert error_data["max_size_bytes"] == 1024
            assert error_data["received_size_bytes"] > 1024

    def test_default_limit_accepts_reasonable_requests(self, app_with_default_limit):
        """Should accept reasonably-sized requests with default limit (10MB)."""
        with TestClient(app_with_default_limit) as client:
            # A moderately large but reasonable request (under 10MB)
            medium_content = "x" * 50000  # ~50KB
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": medium_content}],
                    "max_tokens": 10,
                },
                headers={"Authorization": "Bearer test-key"},
            )

            # Should NOT get 413
            assert response.status_code != 413

    def test_get_requests_not_blocked(self, app_with_small_limit):
        """Should not block GET requests regardless of size limit."""
        with TestClient(app_with_small_limit) as client:
            # Health check endpoint (GET)
            response = client.get("/health")

            # Should succeed
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    def test_streaming_request_size_limit(self, app_with_small_limit):
        """Should enforce size limits on streaming requests."""
        with TestClient(app_with_small_limit) as client:
            large_content = "x" * 2000
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": large_content}],
                    "stream": True,
                },
                headers={"Authorization": "Bearer test-key"},
            )

            # Streaming requests should also be size-limited
            assert response.status_code == 413
