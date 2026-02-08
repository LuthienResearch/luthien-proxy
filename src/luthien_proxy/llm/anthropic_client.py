"""Anthropic SDK client wrapper for making API calls."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

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

        Creates the AsyncAnthropic client immediately for thread safety.

        Args:
            api_key: Anthropic API key for authentication.
            base_url: Optional custom base URL for the API.
        """
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    @staticmethod
    def _sanitize_cache_control(obj: Any) -> Any:
        """Strip extra fields from cache_control, keeping only 'type'.

        The Anthropic API only accepts cache_control: {"type": "ephemeral"}.
        Clients (e.g. Claude Code) may send extra fields like "scope" that
        cause 400 errors. This strips unsupported fields.
        """
        if "cache_control" in obj and isinstance(obj["cache_control"], dict):
            cc = obj["cache_control"]
            if len(cc) > 1 and "type" in cc:
                obj = {**obj, "cache_control": {"type": cc["type"]}}
        return obj

    def _prepare_request_kwargs(self, request: AnthropicRequest) -> dict:
        """Extract non-None values from request for SDK call.

        The Anthropic SDK uses Omit sentinels for optional parameters,
        so we only pass keys that are explicitly set in the request.
        Also sanitizes cache_control fields that may contain extra properties
        not accepted by the Anthropic API.
        """
        kwargs: dict = {
            "model": request["model"],
            "messages": request["messages"],
            "max_tokens": request["max_tokens"],
        }

        # Optional fields - only include if present in request
        if "system" in request:
            system = request["system"]
            if isinstance(system, list):
                system = [self._sanitize_cache_control(block) for block in system]
            kwargs["system"] = system
        if "tools" in request:
            kwargs["tools"] = [self._sanitize_cache_control(tool) for tool in request["tools"]]
        if "tool_choice" in request:
            kwargs["tool_choice"] = request["tool_choice"]
        if "temperature" in request:
            kwargs["temperature"] = request["temperature"]
        if "top_p" in request:
            kwargs["top_p"] = request["top_p"]
        if "top_k" in request:
            kwargs["top_k"] = request["top_k"]
        if "stop_sequences" in request:
            kwargs["stop_sequences"] = request["stop_sequences"]
        if "metadata" in request:
            kwargs["metadata"] = request["metadata"]
        if "thinking" in request:
            kwargs["thinking"] = request["thinking"]

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

            kwargs = self._prepare_request_kwargs(request)
            message = await self._client.messages.create(**kwargs)
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

            kwargs = self._prepare_request_kwargs(request)
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    yield event


__all__ = ["AnthropicClient"]
