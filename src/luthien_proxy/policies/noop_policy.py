"""No-op policy that performs no modifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
    OpenAIPolicyInterface,
)

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class NoOpPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicHookPolicy):
    """No-op policy that passes through all data unchanged.

    Implements OpenAIPolicyInterface and AnthropicHookPolicy.
    All hooks use default passthrough behavior.
    """

    @property
    def short_policy_name(self) -> str:
        """Return 'NoOp'."""
        return "NoOp"

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Pass through unchanged."""
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Pass through unchanged."""
        return response


__all__ = ["NoOpPolicy"]
