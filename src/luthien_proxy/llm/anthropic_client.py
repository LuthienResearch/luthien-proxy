"""Anthropic SDK client wrapper for making API calls."""

from collections.abc import AsyncIterator

import anthropic
import anthropic.types
from anthropic.types import RawMessageStreamEvent
from opentelemetry import trace

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse, build_usage

tracer = trace.get_tracer(__name__)


class AnthropicClient:
    """Client wrapper for Anthropic SDK.

    Provides async methods for both streaming and non-streaming completions
    using the Anthropic Messages API.
    """

    def __init__(
        self,
        api_key: str | None = None,
        auth_token: str | None = None,
        base_url: str | None = None,
    ):
        """Initialize the Anthropic client.

        Creates the AsyncAnthropic client immediately for thread safety.
        Exactly one of api_key or auth_token must be provided.

        Args:
            api_key: Anthropic API key (sent as x-api-key header).
            auth_token: OAuth/bearer token (sent as Authorization: Bearer header).
            base_url: Optional custom base URL for the API.
        """
        if api_key is None and auth_token is None:
            raise ValueError("Either api_key or auth_token must be provided")
        self._base_url = base_url
        kwargs: dict = {}
        if api_key is not None:
            kwargs["api_key"] = api_key
        else:
            kwargs["auth_token"] = auth_token
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)
        if auth_token is not None:
            # The SDK reads ANTHROPIC_API_KEY from the environment and sends it as
            # x-api-key alongside the bearer token. Clear it so only bearer auth is sent.
            self._client.api_key = None

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.close()

    def with_api_key(self, api_key: str) -> "AnthropicClient":
        """Create a new client with a different API key, preserving base_url."""
        return AnthropicClient(api_key=api_key, base_url=self._base_url)

    def with_auth_token(self, auth_token: str) -> "AnthropicClient":
        """Create a new client with a bearer/OAuth token, preserving base_url."""
        return AnthropicClient(auth_token=auth_token, base_url=self._base_url)

    # Fields the Anthropic SDK accepts as named parameters to messages.create().
    # Any request field NOT in this set is forwarded via extra_body so the proxy
    # stays transparent as the API evolves (new fields, beta features, etc.).
    #
    # Note: "stream" is listed here so it doesn't leak into extra_body, but it
    # is intentionally NOT forwarded as a kwarg — the caller controls streaming
    # by choosing complete() vs stream(), not via a request field.
    _SDK_KNOWN_FIELDS: frozenset[str] = frozenset(
        {
            "model",
            "messages",
            "max_tokens",
            "system",
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "metadata",
            "thinking",
            "stream",
        }
    )

    def _prepare_request_kwargs(self, request: AnthropicRequest) -> dict:
        """Extract request fields for SDK call, forwarding unknown fields via extra_body.

        Known SDK parameters are passed as named kwargs. Any additional fields
        present in the request (e.g. output_config, service_tier, container)
        are forwarded via extra_body so they reach the Anthropic API even if
        the proxy's SDK version doesn't have explicit support for them yet.
        """
        kwargs: dict = {
            "model": request["model"],
            "messages": request["messages"],
            "max_tokens": request["max_tokens"],
        }

        # Optional SDK-known fields — only include if present.
        # Use dict-level access to avoid Pyright reportTypedDictNotRequiredAccess
        # (these fields are checked with `in` before access).
        raw: dict = request  # type: ignore[assignment]
        for field in (
            "system",
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "metadata",
            "thinking",
        ):
            if field in raw:
                kwargs[field] = raw[field]

        # Forward any fields the SDK doesn't know about via extra_body.
        # This ensures new API features (output_config, service_tier, etc.)
        # aren't silently dropped by the proxy.
        extra_body = {k: v for k, v in request.items() if k not in self._SDK_KNOWN_FIELDS}
        if extra_body:
            kwargs["extra_body"] = extra_body

        return kwargs

    def _message_to_response(self, message: anthropic.types.Message) -> AnthropicResponse:
        """Convert SDK Message to AnthropicResponse TypedDict."""
        content_blocks = []
        for block in message.content:
            content_blocks.append(block.model_dump())

        return AnthropicResponse(
            id=message.id,
            type="message",
            role="assistant",
            content=content_blocks,
            model=message.model,
            stop_reason=message.stop_reason,
            stop_sequence=message.stop_sequence,
            usage=build_usage(
                message.usage.input_tokens,
                message.usage.output_tokens,
                message.usage.cache_creation_input_tokens,
                message.usage.cache_read_input_tokens,
            ),
        )

    async def complete(
        self, request: AnthropicRequest, extra_headers: dict[str, str] | None = None
    ) -> AnthropicResponse:
        """Get complete response from Anthropic API.

        Uses streaming internally and accumulates the final message.
        The Anthropic API requires streaming for sufficiently long responses
        (high max_tokens) — using messages.stream() avoids errors from the
        API rejecting non-streaming requests that would exceed the limit.

        Args:
            request: Anthropic Messages API request.
            extra_headers: Additional headers to forward to the API (e.g. anthropic-beta).

        Returns:
            AnthropicResponse with the complete message.
        """
        with tracer.start_as_current_span("anthropic.complete") as span:
            span.set_attribute("llm.model", request["model"])
            span.set_attribute("llm.stream", False)

            kwargs = self._prepare_request_kwargs(request)
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
            async with self._client.messages.stream(**kwargs) as stream:
                message = await stream.get_final_message()
            return self._message_to_response(message)

    async def stream(
        self, request: AnthropicRequest, extra_headers: dict[str, str] | None = None
    ) -> AsyncIterator[RawMessageStreamEvent]:
        """Stream response from Anthropic API.

        Uses messages.create(stream=True) to get raw wire-protocol events only,
        avoiding the SDK's high-level MessageStream which injects synthetic
        convenience events (text, thinking, citation, etc.) that have no
        wire-protocol counterpart.

        Args:
            request: Anthropic Messages API request.
            extra_headers: Additional headers to forward to the API (e.g. anthropic-beta).

        Yields:
            Raw streaming events matching the Anthropic wire protocol.
        """
        with tracer.start_as_current_span("anthropic.stream") as span:
            span.set_attribute("llm.model", request["model"])
            span.set_attribute("llm.stream", True)

            kwargs = self._prepare_request_kwargs(request)
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
            stream = await self._client.messages.create(**kwargs, stream=True)
            async with stream:
                async for event in stream:
                    yield event


__all__ = ["AnthropicClient"]
