"""Tests for the BackendAPIError and HTTPException exception handlers in main.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.main import http_status_to_anthropic_error_type
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
                client_format=ClientFormat.ANTHROPIC,
            )

        client = TestClient(app)
        response = client.get("/trigger-429")
        assert response.status_code == 429
        mock_cm.on_backend_401.assert_not_awaited()


class TestHttpStatusToAnthropicErrorType:
    @pytest.mark.parametrize(
        "status_code,expected_type",
        [
            (400, "invalid_request_error"),
            (401, "authentication_error"),
            (403, "permission_error"),
            (404, "not_found_error"),
            (413, "invalid_request_error"),
            (429, "rate_limit_error"),
            (500, "api_error"),
            (503, "overloaded_error"),
            (529, "overloaded_error"),
            (418, "api_error"),  # unmapped status code falls back to api_error
        ],
    )
    def test_status_code_mapping(self, status_code, expected_type):
        assert http_status_to_anthropic_error_type(status_code) == expected_type


class TestHTTPExceptionAnthropicFormat:
    @pytest.fixture
    def app_with_handlers(self):
        from luthien_proxy.main import http_exception_handler

        app = FastAPI()
        app.add_exception_handler(HTTPException, http_exception_handler)

        @app.post("/v1/messages")
        async def anthropic_endpoint():
            raise HTTPException(status_code=401, detail="Missing API key")

        @app.post("/v1/messages/count_tokens")
        async def anthropic_count_tokens():
            raise HTTPException(status_code=400, detail="Invalid request")

        @app.post("/v1/messages")
        async def openai_endpoint():
            raise HTTPException(status_code=401, detail="Missing API key")

        @app.get("/health")
        async def health():
            raise HTTPException(status_code=500, detail="Unhealthy")

        return app

    @pytest.fixture
    def client(self, app_with_handlers):
        return TestClient(app_with_handlers)

    def test_anthropic_path_returns_anthropic_format(self, client):
        response = client.post("/v1/messages")
        assert response.status_code == 401
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "authentication_error"
        assert data["error"]["message"] == "Missing API key"

    def test_anthropic_subpath_returns_anthropic_format(self, client):
        response = client.post("/v1/messages/count_tokens")
        assert response.status_code == 400
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "invalid_request_error"
        assert data["error"]["message"] == "Invalid request"

    def test_non_api_path_returns_default_format(self, client):
        response = client.get("/health")
        assert response.status_code == 500
        data = response.json()
        assert data == {"detail": "Unhealthy"}

    def test_anthropic_413_maps_to_invalid_request(self):
        from luthien_proxy.main import http_exception_handler

        app = FastAPI()
        app.add_exception_handler(HTTPException, http_exception_handler)

        @app.post("/v1/messages")
        async def trigger_413():
            raise HTTPException(status_code=413, detail="Request payload too large")

        test_client = TestClient(app)
        response = test_client.post("/v1/messages")
        assert response.status_code == 413
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "invalid_request_error"
        assert data["error"]["message"] == "Request payload too large"

    def test_non_anthropic_path_preserves_exception_headers(self):
        """Exception headers (e.g. WWW-Authenticate) are forwarded on non-Anthropic paths."""
        from luthien_proxy.main import http_exception_handler

        app = FastAPI()
        app.add_exception_handler(HTTPException, http_exception_handler)

        @app.get("/some/endpoint")
        async def trigger():
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        test_client = TestClient(app)
        response = test_client.get("/some/endpoint")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestRequestValidationErrorHandler:
    """Tests for the RequestValidationError handler returning Anthropic format."""

    @pytest.fixture
    def app_with_validation_handler(self):
        from luthien_proxy.main import http_exception_handler, request_validation_error_handler

        app = FastAPI()
        app.add_exception_handler(HTTPException, http_exception_handler)
        app.add_exception_handler(RequestValidationError, request_validation_error_handler)

        class MessageBody(BaseModel):
            model: str
            max_tokens: int
            messages: list

        @app.post("/v1/messages")
        async def anthropic_endpoint(body: MessageBody):
            return {"ok": True}

        class CompletionBody(BaseModel):
            model: str
            messages: list

        @app.post("/v1/messages")
        async def openai_endpoint(body: CompletionBody):
            return {"ok": True}

        return app

    @pytest.fixture
    def client(self, app_with_validation_handler):
        return TestClient(app_with_validation_handler)

    def test_anthropic_path_returns_anthropic_format_on_validation_error(self, client):
        """Malformed body on /v1/messages returns Anthropic error format, not FastAPI default."""
        response = client.post("/v1/messages", json={"bad": "data"})
        assert response.status_code == 422
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "invalid_request_error"
        assert isinstance(data["error"]["message"], str)
        assert len(data["error"]["message"]) > 0

    def test_anthropic_subpath_returns_anthropic_format(self):
        """Validation errors on /v1/messages/* subpaths also get Anthropic format."""
        from luthien_proxy.main import http_exception_handler, request_validation_error_handler

        app = FastAPI()
        app.add_exception_handler(HTTPException, http_exception_handler)
        app.add_exception_handler(RequestValidationError, request_validation_error_handler)

        class TokenCountBody(BaseModel):
            model: str
            messages: list

        @app.post("/v1/messages/count_tokens")
        async def count_tokens(body: TokenCountBody):
            return {"ok": True}

        test_client = TestClient(app)
        response = test_client.post("/v1/messages/count_tokens", json={})
        assert response.status_code == 422
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "invalid_request_error"
