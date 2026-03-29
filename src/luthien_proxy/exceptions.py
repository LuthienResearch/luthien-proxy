"""Custom exceptions for the Luthien proxy.

This module defines exceptions that can be raised during request processing
and caught by FastAPI exception handlers to return properly formatted error
responses to clients.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from luthien_proxy.pipeline.client_format import ClientFormat


class BackendAPIError(Exception):
    """Wraps backend LLM API errors with client format context.

    When the backend LLM provider (Anthropic, etc.) returns an error,
    this exception captures the error details along with the client format
    so the exception handler can return a properly formatted response.

    Attributes:
        status_code: HTTP status code to return to the client
        message: Error message from the backend
        error_type: Error type string (e.g., "authentication_error", "rate_limit_error")
        client_format: The client's API format (ANTHROPIC)
        provider: The backend provider that raised the error (optional)
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        error_type: str,
        client_format: ClientFormat,
        provider: str | None = None,
    ):
        """Initialize the exception with error details and client format."""
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.client_format = client_format
        self.provider = provider

    def __repr__(self) -> str:
        """Return a string representation for debugging."""
        return (
            f"BackendAPIError(status_code={self.status_code}, error_type={self.error_type!r}, message={self.message!r})"
        )


__all__ = ["BackendAPIError"]
