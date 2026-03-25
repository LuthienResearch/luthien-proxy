"""Tests for custom exceptions module."""

import pytest

from luthien_proxy.exceptions import BackendAPIError
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
            client_format=ClientFormat.ANTHROPIC,
        )

        assert exc.provider is None

    def test_inherits_from_exception(self):
        """BackendAPIError is a proper Exception subclass."""
        exc = BackendAPIError(
            status_code=400,
            message="bad request",
            error_type="invalid_request_error",
            client_format=ClientFormat.ANTHROPIC,
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
