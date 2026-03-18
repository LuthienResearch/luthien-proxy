"""Client format enumeration for request/response handling."""

from enum import Enum


class ClientFormat(str, Enum):
    """Supported client API formats.

    This enum tracks the original client format for proper
    response conversion at the egress boundary.
    """

    ANTHROPIC = "anthropic"


__all__ = ["ClientFormat"]
