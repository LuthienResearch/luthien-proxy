"""Tests for custom exceptions module."""

import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
)

from luthien_proxy.exceptions import BackendAPIError, map_litellm_error_type
from luthien_proxy.pipeline.client_format import ClientFormat


class TestBackendAPIError:
    """Tests for BackendAPIError exception class."""

    def test_basic_construction(self):
        """Error stores all provided attributes."""
        exc = BackendAPIError(
            status_code=401,
            message="invalid api key",
            error_type="authentication_error",
            client_format=ClientFormat.ANTHROPIC,
            provider="anthropic",
        )

        assert exc.status_code == 401
        assert exc.message == "invalid api key"
        assert exc.error_type == "authentication_error"
        assert exc.client_format == ClientFormat.ANTHROPIC
        assert exc.provider == "anthropic"

    def test_optional_provider(self):
        """Provider defaults to None if not specified."""
        exc = BackendAPIError(
            status_code=500,
            message="server error",
            error_type="api_error",
            client_format=ClientFormat.OPENAI,
        )

        assert exc.provider is None

    def test_inherits_from_exception(self):
        """BackendAPIError is a proper Exception subclass."""
        exc = BackendAPIError(
            status_code=400,
            message="bad request",
            error_type="invalid_request_error",
            client_format=ClientFormat.OPENAI,
        )

        assert isinstance(exc, Exception)
        assert str(exc) == "bad request"

    def test_repr(self):
        """Repr provides useful debugging info."""
        exc = BackendAPIError(
            status_code=429,
            message="rate limited",
            error_type="rate_limit_error",
            client_format=ClientFormat.ANTHROPIC,
        )

        repr_str = repr(exc)
        assert "BackendAPIError" in repr_str
        assert "429" in repr_str
        assert "rate_limit_error" in repr_str
        assert "rate limited" in repr_str

    def test_can_be_raised_and_caught(self):
        """Exception can be raised and caught normally."""
        with pytest.raises(BackendAPIError) as exc_info:
            raise BackendAPIError(
                status_code=503,
                message="service unavailable",
                error_type="overloaded_error",
                client_format=ClientFormat.ANTHROPIC,
            )

        assert exc_info.value.status_code == 503


class TestMapLitellmErrorType:
    """Tests for map_litellm_error_type function."""

    def test_authentication_error(self):
        """AuthenticationError maps to authentication_error."""
        exc = AuthenticationError(message="bad key", llm_provider="anthropic", model="claude")
        assert map_litellm_error_type(exc) == "authentication_error"

    def test_rate_limit_error(self):
        """RateLimitError maps to rate_limit_error."""
        exc = RateLimitError(message="too many requests", llm_provider="openai", model="gpt-4")
        assert map_litellm_error_type(exc) == "rate_limit_error"

    def test_bad_request_error(self):
        """BadRequestError maps to invalid_request_error."""
        exc = BadRequestError(message="invalid params", llm_provider="anthropic", model="claude")
        assert map_litellm_error_type(exc) == "invalid_request_error"

    def test_api_connection_error(self):
        """APIConnectionError maps to api_connection_error."""
        exc = APIConnectionError(message="connection failed", llm_provider="openai", model="gpt-4")
        assert map_litellm_error_type(exc) == "api_connection_error"

    def test_service_unavailable_error(self):
        """ServiceUnavailableError maps to overloaded_error."""
        exc = ServiceUnavailableError(message="try again", llm_provider="anthropic", model="claude")
        assert map_litellm_error_type(exc) == "overloaded_error"

    def test_internal_server_error(self):
        """InternalServerError maps to api_error."""
        exc = InternalServerError(message="internal error", llm_provider="openai", model="gpt-4")
        assert map_litellm_error_type(exc) == "api_error"

    def test_unknown_exception_returns_api_error(self):
        """Unknown exception types default to api_error."""

        class CustomError(Exception):
            pass

        assert map_litellm_error_type(CustomError("unknown")) == "api_error"

    def test_standard_exception_returns_api_error(self):
        """Standard Python exceptions map to api_error."""
        assert map_litellm_error_type(ValueError("bad value")) == "api_error"
        assert map_litellm_error_type(RuntimeError("runtime issue")) == "api_error"
