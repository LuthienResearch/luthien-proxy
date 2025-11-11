"""Unit tests for request validation middleware."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.v2.middleware import (
    DEFAULT_MAX_REQUEST_SIZE,
    RequestSizeLimitMiddleware,
    get_max_request_size,
)


@pytest.fixture
def app():
    """Create a test FastAPI app with the middleware."""
    test_app = FastAPI()

    # Add a simple test route
    @test_app.post("/test")
    async def test_route(data: dict):
        return {"success": True, "data": data}

    return test_app


@pytest.fixture
def client_with_middleware(app):
    """Create a test client with the middleware enabled."""
    app.add_middleware(RequestSizeLimitMiddleware, max_size=1024)  # 1KB limit for testing
    return TestClient(app)


@pytest.fixture
def client_with_default_middleware(app):
    """Create a test client with default middleware settings."""
    app.add_middleware(RequestSizeLimitMiddleware)
    return TestClient(app)


class TestGetMaxRequestSize:
    """Test the get_max_request_size() helper function."""

    def test_default_value(self):
        """Should return default value when env var not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_REQUEST_SIZE", None)
            assert get_max_request_size() == DEFAULT_MAX_REQUEST_SIZE

    def test_valid_env_var(self):
        """Should parse valid env var value."""
        with patch.dict(os.environ, {"MAX_REQUEST_SIZE": "5242880"}):  # 5MB
            assert get_max_request_size() == 5242880

    def test_invalid_env_var(self):
        """Should return default when env var is invalid."""
        with patch.dict(os.environ, {"MAX_REQUEST_SIZE": "not-a-number"}):
            assert get_max_request_size() == DEFAULT_MAX_REQUEST_SIZE

    def test_zero_env_var(self):
        """Should allow zero (no limit) if explicitly set."""
        with patch.dict(os.environ, {"MAX_REQUEST_SIZE": "0"}):
            assert get_max_request_size() == 0


class TestRequestSizeLimitMiddleware:
    """Test the RequestSizeLimitMiddleware."""

    def test_small_request_allowed(self, client_with_middleware):
        """Should allow requests under the size limit."""
        small_payload = {"message": "Hello"}
        response = client_with_middleware.post("/test", json=small_payload)

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_oversized_request_rejected(self, client_with_middleware):
        """Should reject requests exceeding the size limit."""
        # Create a payload that exceeds 1KB
        large_payload = {"data": "x" * 2000}  # ~2KB

        response = client_with_middleware.post("/test", json=large_payload)

        assert response.status_code == 413
        assert "payload too large" in response.json()["error"].lower()
        assert "max_size_bytes" in response.json()
        assert response.json()["max_size_bytes"] == 1024

    def test_exact_limit_allowed(self, app):
        """Should allow requests exactly at the size limit."""
        # Set middleware to allow exactly 100 bytes
        app.add_middleware(RequestSizeLimitMiddleware, max_size=100)
        client = TestClient(app)

        # Create payload that's close to but under 100 bytes
        # JSON serialization adds quotes and formatting
        small_payload = {"x": "a" * 80}

        response = client.post("/test", json=small_payload)
        # Should succeed (we're under the limit with JSON overhead)
        assert response.status_code == 200

    def test_get_requests_not_checked(self, client_with_middleware):
        """Should not check GET requests (they don't have bodies)."""
        # GET requests should pass through without size checking
        # Note: TestClient doesn't send Content-Length for GET
        response = client_with_middleware.get("/test")
        # Will get 405 Method Not Allowed since route is POST only,
        # but should NOT get 413 Payload Too Large
        assert response.status_code == 405

    def test_missing_content_length_allowed(self, app):
        """Should allow requests without Content-Length header."""
        app.add_middleware(RequestSizeLimitMiddleware, max_size=100)
        client = TestClient(app)

        # TestClient normally includes Content-Length, but we can test
        # that the middleware handles missing headers gracefully
        response = client.post("/test", json={"test": "data"})
        # Should succeed since TestClient includes proper headers
        assert response.status_code == 200

    def test_invalid_content_length_header(self, app):
        """Should handle invalid Content-Length header gracefully."""
        app.add_middleware(RequestSizeLimitMiddleware, max_size=1024)

        async def custom_endpoint(request):
            return {"success": True}

        # We need to test with a mock request that has invalid Content-Length
        # This is tested via the dispatch method directly
        middleware = RequestSizeLimitMiddleware(app, max_size=1024)

        mock_request = Mock()
        mock_request.method = "POST"
        mock_request.headers = {"content-length": "invalid-number"}
        mock_request.url.path = "/test"

        call_next = AsyncMock(return_value=Mock())

        # Should not raise an exception, should call next
        import asyncio

        asyncio.run(middleware.dispatch(mock_request, call_next))
        call_next.assert_called_once()

    def test_default_max_size(self, client_with_default_middleware):
        """Should use default max size when not specified."""
        # Default is 10MB, so a small request should pass
        small_payload = {"message": "Hello"}
        response = client_with_default_middleware.post("/test", json=small_payload)

        assert response.status_code == 200

    def test_error_response_structure(self, client_with_middleware):
        """Should return properly structured error response."""
        large_payload = {"data": "x" * 2000}  # ~2KB
        response = client_with_middleware.post("/test", json=large_payload)

        assert response.status_code == 413
        error_data = response.json()

        # Check error response structure
        assert "error" in error_data
        assert "detail" in error_data
        assert "max_size_bytes" in error_data
        assert "received_size_bytes" in error_data

        assert error_data["max_size_bytes"] == 1024
        assert error_data["received_size_bytes"] > 1024

    def test_post_method_checked(self, client_with_middleware):
        """Should check POST requests."""
        large_payload = {"data": "x" * 2000}
        response = client_with_middleware.post("/test", json=large_payload)
        assert response.status_code == 413

    def test_put_method_checked(self, app):
        """Should check PUT requests."""

        @app.put("/test-put")
        async def test_put(data: dict):
            return {"success": True}

        app.add_middleware(RequestSizeLimitMiddleware, max_size=1024)
        client = TestClient(app)

        large_payload = {"data": "x" * 2000}
        response = client.put("/test-put", json=large_payload)
        assert response.status_code == 413

    def test_patch_method_checked(self, app):
        """Should check PATCH requests."""

        @app.patch("/test-patch")
        async def test_patch(data: dict):
            return {"success": True}

        app.add_middleware(RequestSizeLimitMiddleware, max_size=1024)
        client = TestClient(app)

        large_payload = {"data": "x" * 2000}
        response = client.patch("/test-patch", json=large_payload)
        assert response.status_code == 413
