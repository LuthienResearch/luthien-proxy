# ABOUTME: Middleware for request validation and security controls
# ABOUTME: Includes request size limiting to prevent DoS attacks

"""FastAPI middleware for request validation and security."""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Default max request size: 10MB
DEFAULT_MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB in bytes


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce maximum request body size.

    This prevents DoS attacks via oversized payloads by checking the
    Content-Length header before reading the request body.

    Args:
        app: The FastAPI application
        max_size: Maximum allowed request body size in bytes (default: 10MB)
    """

    def __init__(self, app, max_size: int = DEFAULT_MAX_REQUEST_SIZE):
        """Initialize the middleware.

        Args:
            app: The FastAPI application
            max_size: Maximum allowed request body size in bytes
        """
        super().__init__(app)
        self.max_size = max_size
        logger.info(f"RequestSizeLimitMiddleware initialized with max_size={max_size:,} bytes")

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Check request size before processing.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or route handler

        Returns:
            Response from the next handler, or 413 error if request is too large
        """
        # Only check requests with bodies (POST, PUT, PATCH)
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")

            if content_length:
                try:
                    content_length_int = int(content_length)
                    if content_length_int > self.max_size:
                        # Log the rejection with details
                        logger.warning(
                            f"Request rejected: size={content_length_int:,} bytes exceeds "
                            f"max={self.max_size:,} bytes. Path={request.url.path}, "
                            f"Method={request.method}"
                        )

                        return JSONResponse(
                            status_code=413,
                            content={
                                "error": "Request payload too large",
                                "detail": f"Request size {content_length_int:,} bytes exceeds maximum allowed size of {self.max_size:,} bytes",
                                "max_size_bytes": self.max_size,
                                "received_size_bytes": content_length_int,
                            },
                        )
                except ValueError:
                    # Invalid Content-Length header - let it through and let
                    # downstream validation handle it
                    logger.warning(f"Invalid Content-Length header: {content_length}")

        # Request size is acceptable, continue processing
        return await call_next(request)


def get_max_request_size() -> int:
    """Get maximum request size from environment variable.

    Returns:
        Maximum request size in bytes (default: 10MB)
    """
    max_size_str = os.getenv("MAX_REQUEST_SIZE")
    if max_size_str:
        try:
            return int(max_size_str)
        except ValueError:
            logger.warning(f"Invalid MAX_REQUEST_SIZE value: {max_size_str}. Using default: {DEFAULT_MAX_REQUEST_SIZE}")
            return DEFAULT_MAX_REQUEST_SIZE
    return DEFAULT_MAX_REQUEST_SIZE


__all__ = ["RequestSizeLimitMiddleware", "get_max_request_size", "DEFAULT_MAX_REQUEST_SIZE"]
