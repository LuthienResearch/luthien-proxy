"""Anthropic SDK client wrapper for making API calls."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import anthropic
from opentelemetry import trace

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse

if TYPE_CHECKING:
    from anthropic.lib.streaming import MessageStreamEvent

tracer = trace.get_tracer(__name__)


class AnthropicClient:
    """Client wrapper for Anthropic SDK.

    Provides async methods for both streaming and non-streaming completions
    using the Anthropic Messages API.
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        """Initialize the Anthropic client.

        Args:
            api_key: Anthropic API key for authentication.
            base_url: Optional custom base URL for the API.
        """
        self._api_key = api_key
        self._base_url = base_url
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        """Get or create the cached AsyncAnthropic client instance.

        Uses lazy initialization to create the client on first use,
        then returns the same instance for connection pooling benefits.
        """
        if self._client is None:
            kwargs: dict = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    def _prepare_request_kwargs(self, request: AnthropicRequest) -> dict:
        """Extract non-None values from request for SDK call.

        The Anthropic SDK uses Omit sentinels for optional parameters,
        so we only pass keys that are explicitly set in the request.
        """
        kwargs: dict = {}

        # Required fields
        kwargs["model"] = request["model"]
        kwargs["messages"] = request["messages"]
        kwargs["max_tokens"] = request["max_tokens"]

        # Optional fields - only include if present
        optional_keys = [
            "system",
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "metadata",
            "thinking",
        ]
        for key in optional_keys:
            if key in request:
                kwargs[key] = request[key]  # type: ignore[literal-required]

        return kwargs

    def _message_to_response(self, message: anthropic.types.Message) -> AnthropicResponse:
        """Convert SDK Message to AnthropicResponse TypedDict."""
        content_blocks = []
        for block in message.content:
            block_dict = block.model_dump()
            content_blocks.append(block_dict)

        return AnthropicResponse(
            id=message.id,
            type="message",
            role="assistant",
            content=content_blocks,
            model=message.model,
            stop_reason=message.stop_reason,
            stop_sequence=message.stop_sequence,
            usage={
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        )

    async def complete(self, request: AnthropicRequest) -> AnthropicResponse:
        """Get complete response from Anthropic API.

        Args:
            request: Anthropic Messages API request.

        Returns:
            AnthropicResponse with the complete message.
        """
        with tracer.start_as_current_span("anthropic.complete") as span:
            span.set_attribute("llm.model", request["model"])
            span.set_attribute("llm.stream", False)

            client = self._get_client()
            kwargs = self._prepare_request_kwargs(request)

            message = await client.messages.create(**kwargs)
            return self._message_to_response(message)

    async def stream(self, request: AnthropicRequest) -> AsyncIterator["MessageStreamEvent"]:
        """Stream response from Anthropic API.

        Args:
            request: Anthropic Messages API request.

        Yields:
            Streaming events from the Anthropic SDK (includes text, thinking, etc.).
        """
        with tracer.start_as_current_span("anthropic.stream") as span:
            span.set_attribute("llm.model", request["model"])
            span.set_attribute("llm.stream", True)

            client = self._get_client()
            kwargs = self._prepare_request_kwargs(request)

            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    yield event


__all__ = ["AnthropicClient"]
