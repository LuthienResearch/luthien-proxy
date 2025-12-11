"""Client format enumeration for request/response handling."""

from enum import Enum


class ClientFormat(str, Enum):
    """Supported client API formats.

    The gateway processes requests in OpenAI format internally.
    This enum tracks the original client format for proper
    response conversion at the egress boundary.
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


__all__ = ["ClientFormat"]
