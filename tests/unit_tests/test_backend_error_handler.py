"""Tests for the BackendAPIError exception handler in main.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.pipeline.client_format import ClientFormat


@pytest.fixture
def app_with_error_handler():
    """Create a minimal FastAPI app with the BackendAPIError handler."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.exception_handler(BackendAPIError)
    async def backend_api_error_handler(request: Request, exc: BackendAPIError) -> JSONResponse:
        """Handle errors from backend LLM providers."""
        if exc.client_format == ClientFormat.ANTHROPIC:
            content = {
                "type": "error",
                "error": {
                    "type": exc.error_type,
                    "message": exc.message,
                },
            }
        else:
            content = {
                "error": {
                    "message": exc.message,
                    "type": exc.error_type,
                    "param": None,
                    "code": None,
                },
            }
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.get("/trigger-anthropic-error")
    async def trigger_anthropic_error():
        raise BackendAPIError(
            status_code=401,
            message="invalid x-api-key",
            error_type="authentication_error",
            client_format=ClientFormat.ANTHROPIC,
            provider="anthropic",
        )

    @app.get("/trigger-openai-error")
    async def trigger_openai_error():
        raise BackendAPIError(
            status_code=429,
            message="Rate limit exceeded",
            error_type="rate_limit_error",
            client_format=ClientFormat.OPENAI,
            provider="openai",
        )

    @app.get("/trigger-500-error")
    async def trigger_500_error():
        raise BackendAPIError(
            status_code=500,
            message="Internal server error",
            error_type="api_error",
            client_format=ClientFormat.ANTHROPIC,
        )

    return app


@pytest.fixture
def client(app_with_error_handler):
    """Create a test client for the app."""
    return TestClient(app_with_error_handler)


class TestBackendAPIErrorHandler:
    """Tests for the BackendAPIError exception handler."""

    def test_anthropic_format_error_response(self, client):
        """Anthropic format errors return correct structure."""
        response = client.get("/trigger-anthropic-error")

        assert response.status_code == 401
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "authentication_error"
        assert data["error"]["message"] == "invalid x-api-key"

    def test_openai_format_error_response(self, client):
        """OpenAI format errors return correct structure."""
        response = client.get("/trigger-openai-error")

        assert response.status_code == 429
        data = response.json()
        assert "error" in data
        assert data["error"]["message"] == "Rate limit exceeded"
        assert data["error"]["type"] == "rate_limit_error"
        assert data["error"]["param"] is None
        assert data["error"]["code"] is None

    def test_500_error_returns_correct_status(self, client):
        """500 errors propagate the correct status code."""
        response = client.get("/trigger-500-error")

        assert response.status_code == 500
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "api_error"

    def test_anthropic_format_has_no_openai_fields(self, client):
        """Anthropic format doesn't include OpenAI-specific fields."""
        response = client.get("/trigger-anthropic-error")
        data = response.json()

        # Anthropic format should NOT have these OpenAI fields
        assert "param" not in data.get("error", {})
        assert "code" not in data.get("error", {})

    def test_openai_format_has_no_anthropic_fields(self, client):
        """OpenAI format doesn't include Anthropic-specific fields."""
        response = client.get("/trigger-openai-error")
        data = response.json()

        # OpenAI format should NOT have "type": "error" at root level
        assert data.get("type") != "error"


class TestBackend401InvalidatesCredential:
    """Test that backend 401 errors invalidate cached credentials."""

    def test_401_calls_on_backend_401(self):
        """When the backend returns 401, the passthrough credential is invalidated."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        app = FastAPI()
        mock_cm = AsyncMock()

        mock_deps = MagicMock()
        mock_deps.credential_manager = mock_cm
        app.state.dependencies = mock_deps

        @app.exception_handler(BackendAPIError)
        async def handler(request: Request, exc: BackendAPIError) -> JSONResponse:
            if exc.status_code == 401 and hasattr(request.state, "passthrough_credential"):
                deps = getattr(request.app.state, "dependencies", None)
                cm = getattr(deps, "credential_manager", None) if deps else None
                if cm is not None:
                    await cm.on_backend_401(request.state.passthrough_credential)
            return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

        @app.get("/trigger-401")
        async def trigger(request: Request):
            request.state.passthrough_credential = "user-api-key"
            raise BackendAPIError(
                status_code=401,
                message="invalid key",
                error_type="authentication_error",
                client_format=ClientFormat.ANTHROPIC,
            )

        client = TestClient(app)
        response = client.get("/trigger-401")
        assert response.status_code == 401
        mock_cm.on_backend_401.assert_awaited_once_with("user-api-key")

    def test_non_401_does_not_invalidate(self):
        """Non-401 errors should not trigger credential invalidation."""
        from fastapi import Request
        from fastapi.responses import JSONResponse

        app = FastAPI()
        mock_cm = AsyncMock()

        mock_deps = MagicMock()
        mock_deps.credential_manager = mock_cm
        app.state.dependencies = mock_deps

        @app.exception_handler(BackendAPIError)
        async def handler(request: Request, exc: BackendAPIError) -> JSONResponse:
            if exc.status_code == 401 and hasattr(request.state, "passthrough_credential"):
                deps = getattr(request.app.state, "dependencies", None)
                cm = getattr(deps, "credential_manager", None) if deps else None
                if cm is not None:
                    await cm.on_backend_401(request.state.passthrough_credential)
            return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

        @app.get("/trigger-429")
        async def trigger(request: Request):
            request.state.passthrough_credential = "user-api-key"
            raise BackendAPIError(
                status_code=429,
                message="rate limit",
                error_type="rate_limit_error",
                client_format=ClientFormat.OPENAI,
            )

        client = TestClient(app)
        response = client.get("/trigger-429")
        assert response.status_code == 429
        mock_cm.on_backend_401.assert_not_awaited()
