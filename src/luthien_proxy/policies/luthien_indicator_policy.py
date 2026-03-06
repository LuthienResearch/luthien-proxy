"""Policy that appends a 'logged by Luthien' indicator to responses."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from litellm.types.utils import Choices, ModelResponse

from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.chunk_builders import create_text_chunk
from luthien_proxy.policy_core.policy_context import PolicyContext

if TYPE_CHECKING:
    from luthien_proxy.llm.types import Request
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)

INDICATOR_SUFFIX = "\n\n---\n*This conversation is logged and monitored by Luthien.*"


class LuthienIndicatorPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Appends a brief indicator to text responses so users know they're going through Luthien.

    Only modifies text content — tool calls, thinking blocks, and images pass through unchanged.
    Configurable via ``indicator`` in policy config YAML.
    """

    def __init__(self, config: dict | None = None) -> None:
        """Initialize with optional custom indicator text."""
        self._indicator = (config or {}).get("indicator", INDICATOR_SUFFIX)

    @property
    def short_policy_name(self) -> str:
        """Return 'LuthienIndicator'."""
        return "LuthienIndicator"

    # -- OpenAI non-streaming --------------------------------------------------

    async def on_openai_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through request unchanged."""
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Append indicator to text content in each choice."""
        if not response.choices:
            return response

        for choice in response.choices:
            if isinstance(choice, Choices) and isinstance(choice.message.content, str):
                choice.message.content += self._indicator
        return response

    # -- OpenAI streaming ------------------------------------------------------

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Forward chunk unchanged."""
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Inject indicator as a final text chunk."""
        model = ctx.request.model if ctx.request else "luthien-policy"
        indicator_chunk = create_text_chunk(self._indicator, model=model)
        ctx.push_chunk(indicator_chunk)

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""

    # -- Anthropic execution interface -----------------------------------------

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Run Anthropic request, appending indicator to non-streaming responses."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            request = io.request

            if request.get("stream", False):
                async for event in io.stream(request):
                    yield event
                return

            response = await io.complete(request)
            response = self._append_indicator_to_anthropic(response)
            yield response

        return _run()

    def _append_indicator_to_anthropic(self, response: AnthropicResponse) -> AnthropicResponse:
        """Append indicator as a new text block to the Anthropic response."""
        content = list(response.get("content", []))
        content.append({"type": "text", "text": self._indicator})
        response["content"] = content  # type: ignore[typeddict-item]
        return response


__all__ = ["LuthienIndicatorPolicy"]
