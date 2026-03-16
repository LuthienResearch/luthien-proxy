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

    When the backend LLM provider (Anthropic, OpenAI, etc.) returns an error,
    this exception captures the error details along with the client format
    so the exception handler can return a properly formatted response.

    Attributes:
        status_code: HTTP status code to return to the client
        message: Error message from the backend
        error_type: Error type string (e.g., "authentication_error", "rate_limit_error")
        client_format: The client's API format (ANTHROPIC or OPENAI)
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


# Mapping from LiteLLM exception class names to error type strings
# These align with Anthropic's error types where possible
_LITELLM_ERROR_TYPE_MAP = {
    "AuthenticationError": "authentication_error",
    "RateLimitError": "rate_limit_error",
    "BadRequestError": "invalid_request_error",
    "InvalidRequestError": "invalid_request_error",
    "NotFoundError": "not_found_error",
    "PermissionDeniedError": "permission_error",
    "APIConnectionError": "api_connection_error",
    "ServiceUnavailableError": "overloaded_error",
    "InternalServerError": "api_error",
    "ContextWindowExceededError": "invalid_request_error",
    "ContentPolicyViolationError": "invalid_request_error",
    "BillingHardLimitReachedError": "billing_error",
}


# Keywords in upstream error messages that indicate billing/quota issues,
# even when the HTTP status or exception class is generic (e.g. 403 or
# PermissionDeniedError).  Used by ``is_billing_error`` to detect quota
# exhaustion regardless of provider-specific status codes.
_BILLING_KEYWORDS = (
    "billing",
    "quota",
    "insufficient_quota",
    "exceeded your current quota",
    "suspended",
    "deactivated",
    "credit",
    "plan limit",
    "usage limit",
    "spending limit",
)


def is_billing_error(status_code: int, error_type: str, message: str) -> bool:
    """Return True if the error represents a billing/quota issue.

    Detects billing problems via:
    - HTTP 402 (Payment Required)
    - error_type already classified as ``billing_error``
    - Known billing keywords in the upstream message
    """
    if status_code == 402:
        return True
    if error_type == "billing_error":
        return True
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _BILLING_KEYWORDS)


def is_rate_limit_error(status_code: int, error_type: str) -> bool:
    """Return True if the error is a rate limit (not billing/quota)."""
    return status_code == 429 or error_type == "rate_limit_error"


def enrich_billing_message(original_message: str) -> str:
    """Wrap an upstream billing/quota error with actionable guidance."""
    return (
        f"Your API account has hit a billing or usage limit. "
        f"Upstream provider message: {original_message}. "
        f"Please check your account's billing settings, credit balance, "
        f"and usage limits with your API provider."
    )


def enrich_rate_limit_message(original_message: str) -> str:
    """Wrap an upstream rate limit error with actionable guidance."""
    return (
        f"The upstream API provider is rate limiting your requests. "
        f"Upstream provider message: {original_message}. "
        f"Please wait a moment before retrying. If this persists, "
        f"check your API plan's rate limits."
    )


def map_litellm_error_type(exception: Exception) -> str:
    """Map a LiteLLM exception to an error type string.

    Args:
        exception: A LiteLLM exception instance

    Returns:
        An error type string suitable for Anthropic/OpenAI error responses
    """
    class_name = type(exception).__name__
    return _LITELLM_ERROR_TYPE_MAP.get(class_name, "api_error")


__all__ = [
    "BackendAPIError",
    "enrich_billing_message",
    "enrich_rate_limit_message",
    "is_billing_error",
    "is_rate_limit_error",
    "map_litellm_error_type",
]
