"""Anthropic SDK client wrapper with self-healing request pipeline.

Pre-flight sanitization fixes known bad patterns before they reach the API.
Retry-with-fix catches remaining 400s and applies mechanical corrections.
Human-centered error messages guide users when auto-fix isn't possible.
"""

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import anthropic
from opentelemetry import trace

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse

if TYPE_CHECKING:
    from anthropic.lib.streaming import MessageStreamEvent

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# ---------------------------------------------------------------------------
# Pre-flight sanitization helpers
# ---------------------------------------------------------------------------


def _is_empty_text_block(block: Any) -> bool:
    """Check if a content block is an empty or whitespace-only text block.

    The Anthropic API rejects both:
    - Empty text: {"type": "text", "text": ""} -> 'must be non-empty'
    - Whitespace-only: {"type": "text", "text": " "} -> 'must contain non-whitespace text'
    """
    if not isinstance(block, dict) or block.get("type") != "text":
        return False
    text = block.get("text")
    return isinstance(text, str) and text.strip() == ""


def _sanitize_messages(messages: list[Any]) -> list[Any]:
    """Remove empty/whitespace-only text content blocks from messages.

    Some clients (e.g., Claude Code) can produce messages with empty text blocks
    like {"type": "text", "text": ""} which the Anthropic API rejects with
    'messages: text content blocks must be non-empty'. It also rejects
    whitespace-only text with 'must contain non-whitespace text'.

    Only filters blocks from list-style content (not bare string content).
    Preserves messages even if all text blocks are empty (to avoid breaking
    message structure).
    """
    sanitized = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            sanitized.append(msg)
            continue

        filtered = [block for block in content if not _is_empty_text_block(block)]

        if filtered != content:
            logger.debug(
                "Stripped %d empty text block(s) from %s message",
                len(content) - len(filtered),
                msg.get("role", "unknown"),
            )

        # If filtering removed ALL blocks, keep original to avoid
        # breaking message structure (API will reject either way)
        if not filtered:
            sanitized.append(msg)
        elif len(filtered) != len(content):
            sanitized.append({**msg, "content": filtered})
        else:
            sanitized.append(msg)

    return sanitized


def _collect_tool_use_ids(messages: list[Any]) -> set[str]:
    """Collect all tool_use IDs from assistant messages."""
    tool_use_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_id = block.get("id")
                if tool_id:
                    tool_use_ids.add(tool_id)
    return tool_use_ids


def _prune_orphaned_tool_results(messages: list[Any]) -> list[Any]:
    """Remove tool_result blocks whose tool_use_id has no matching tool_use.

    After /compact or context trimming, assistant messages containing tool_use
    blocks may be removed while the corresponding user tool_result blocks remain.
    The Anthropic API rejects these orphaned tool_results.
    """
    tool_use_ids = _collect_tool_use_ids(messages)
    pruned = []

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            pruned.append(msg)
            continue

        filtered = []
        removed_count = 0
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") not in tool_use_ids
            ):
                removed_count += 1
                continue
            filtered.append(block)

        if removed_count > 0:
            logger.info(
                "[auto-fix] Pruned %d orphaned tool_result block(s) from %s message",
                removed_count,
                msg.get("role", "unknown"),
            )

        if not filtered:
            # All blocks were orphaned tool_results — drop the entire message
            logger.info(
                "[auto-fix] Dropped %s message with only orphaned tool_result blocks",
                msg.get("role", "unknown"),
            )
            continue

        if removed_count > 0:
            pruned.append({**msg, "content": filtered})
        else:
            pruned.append(msg)

    return pruned


def _deduplicate_tools(tools: list[Any]) -> list[Any]:
    """Remove duplicate tool definitions, keeping the first occurrence."""
    seen: set[str] = set()
    deduped = []
    for tool in tools:
        name = tool.get("name", "")
        if name in seen:
            continue
        seen.add(name)
        deduped.append(tool)
    if len(deduped) < len(tools):
        logger.info(
            "[auto-fix] Deduplicated tools list: %d → %d",
            len(tools),
            len(deduped),
        )
    return deduped


def _sanitize_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Apply all known pre-flight fixes to request kwargs.

    Called before every API request. Fixes mechanical issues that would
    cause 400 errors. Never changes semantic intent.
    """
    if "messages" in kwargs:
        kwargs["messages"] = _sanitize_messages(kwargs["messages"])
        kwargs["messages"] = _prune_orphaned_tool_results(kwargs["messages"])

    if "tools" in kwargs:
        kwargs["tools"] = _deduplicate_tools(kwargs["tools"])

    return kwargs


# ---------------------------------------------------------------------------
# Retry-with-fix: catch 400s that pre-flight missed
# ---------------------------------------------------------------------------


def _try_auto_fix(kwargs: dict[str, Any], error: anthropic.BadRequestError) -> dict[str, Any] | None:
    """Attempt to fix a 400 error by pattern-matching the error message.

    Returns fixed kwargs if a fix was applied, None if the error is unfixable.
    Never changes semantic intent — only mechanical cleanup.
    """
    msg = str(error.message).lower() if error.message else ""

    # Empty text blocks
    if "text content blocks must be non-empty" in msg or "must contain non-whitespace" in msg:
        fixed = dict(kwargs)
        fixed["messages"] = _sanitize_messages(kwargs["messages"])
        return fixed

    # Orphaned tool_results (tool_use_id doesn't match any tool_use)
    if "tool_use_id" in msg or "tool_result" in msg:
        fixed = dict(kwargs)
        fixed["messages"] = _prune_orphaned_tool_results(kwargs["messages"])
        if fixed["messages"] != kwargs["messages"]:
            return fixed
        return None  # Pruning didn't change anything — can't fix

    # Duplicate tool names
    if "tool names must be unique" in msg:
        if "tools" in kwargs:
            fixed = dict(kwargs)
            fixed["tools"] = _deduplicate_tools(kwargs["tools"])
            return fixed
        return None

    # Context overflow — don't auto-fix, user needs to decide
    if "prompt is too long" in msg or "too many tokens" in msg or "context length" in msg:
        return None

    # Unknown 400 — don't auto-fix
    return None


# ---------------------------------------------------------------------------
# Human-centered error messages (Nielsen's heuristic #9)
# ---------------------------------------------------------------------------

_ISSUE_URL = "github.com/LuthienResearch/luthien-proxy/issues"


def _make_context_overflow_message(model: str) -> str:
    """Error message for context overflow."""
    return (
        f"Your conversation has grown too long for {model}. "
        "Try /compact to summarize older messages, or start a new conversation."
    )


def _make_unknown_400_message(model: str) -> str:
    """Error message for unknown 400 errors."""
    return (
        f"Luthien couldn't process your request for {model}. "
        "Try again, or use /compact to reduce your conversation. "
        f"If this persists, report it at {_ISSUE_URL}"
    )


def _make_server_error_message(model: str) -> str:
    """Error message for 5xx / connection errors."""
    return f"{model} is temporarily unavailable. Try again in a moment."


def _is_context_overflow(error: anthropic.BadRequestError) -> bool:
    """Check if a 400 error is a context overflow."""
    msg = str(error.message).lower() if error.message else ""
    return any(
        phrase in msg
        for phrase in [
            "prompt is too long",
            "too many tokens",
            "context length",
            "exceeds the maximum",
        ]
    )


def _extract_model(kwargs: dict[str, Any]) -> str:
    """Extract model name from kwargs for error messages."""
    return str(kwargs.get("model", "the model"))


def _rewrite_bad_request_error(kwargs: dict[str, Any], error: anthropic.BadRequestError) -> anthropic.BadRequestError:
    """Rewrite a BadRequestError with a human-centered message.

    Logs technical details at DEBUG, returns error with plain-language message.
    """
    model = _extract_model(kwargs)
    logger.debug(
        "Anthropic 400 error (model=%s, messages=%d): %s",
        model,
        len(kwargs.get("messages", [])),
        error.message,
    )

    if _is_context_overflow(error):
        human_msg = _make_context_overflow_message(model)
    else:
        human_msg = _make_unknown_400_message(model)

    return anthropic.BadRequestError(
        message=human_msg,
        response=error.response,
        body=error.body,
    )


def _rewrite_server_error(kwargs: dict[str, Any], error: anthropic.APIStatusError) -> anthropic.APIStatusError:
    """Rewrite a 5xx error with a human-centered message."""
    model = _extract_model(kwargs)
    logger.debug(
        "Anthropic server error (model=%s, status=%d): %s",
        model,
        error.status_code,
        error.message,
    )
    human_msg = _make_server_error_message(model)
    return type(error)(
        message=human_msg,
        response=error.response,
        body=error.body,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AnthropicClient:
    """Client wrapper for Anthropic SDK.

    Provides async methods for both streaming and non-streaming completions
    using the Anthropic Messages API. Includes self-healing:
    - Pre-flight sanitization prevents most 400 errors
    - Retry-with-fix catches remaining fixable 400s (max 1 retry)
    - Human-centered error messages when auto-fix isn't possible
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        """Initialize the Anthropic client.

        Creates the AsyncAnthropic client immediately for thread safety.

        Args:
            api_key: Anthropic API key for authentication.
            base_url: Optional custom base URL for the API.
        """
        self._base_url = base_url
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def with_api_key(self, api_key: str) -> "AnthropicClient":
        """Create a new client with a different API key, preserving base_url."""
        return AnthropicClient(api_key=api_key, base_url=self._base_url)

    def _prepare_request_kwargs(self, request: AnthropicRequest) -> dict:
        """Extract non-None values from request for SDK call.

        The Anthropic SDK uses Omit sentinels for optional parameters,
        so we only pass keys that are explicitly set in the request.
        Applies pre-flight sanitization to prevent known 400 errors.
        """
        kwargs: dict = {
            "model": request["model"],
            "messages": request["messages"],
            "max_tokens": request["max_tokens"],
        }

        # Optional fields - only include if present in request
        if "system" in request:
            kwargs["system"] = request["system"]
        if "tools" in request:
            kwargs["tools"] = request["tools"]
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

        return _sanitize_request(kwargs)

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

        Applies pre-flight sanitization, then retries once if the API returns
        a fixable 400 error. Unfixable errors get human-centered messages.

        Args:
            request: Anthropic Messages API request.

        Returns:
            AnthropicResponse with the complete message.
        """
        with tracer.start_as_current_span("anthropic.complete") as span:
            span.set_attribute("llm.model", request["model"])
            span.set_attribute("llm.stream", False)

            kwargs = self._prepare_request_kwargs(request)
            try:
                message = await self._client.messages.create(**kwargs)
            except anthropic.BadRequestError as e:
                fixed_kwargs = _try_auto_fix(kwargs, e)
                if fixed_kwargs is not None:
                    logger.info("[auto-fix] %s — retrying", e.message)
                    span.set_attribute("luthien.auto_fix", True)
                    message = await self._client.messages.create(**fixed_kwargs)
                else:
                    raise _rewrite_bad_request_error(kwargs, e) from e
            except anthropic.InternalServerError as e:
                raise _rewrite_server_error(kwargs, e) from e

            return self._message_to_response(message)

    async def stream(self, request: AnthropicRequest) -> AsyncIterator["MessageStreamEvent"]:
        """Stream response from Anthropic API.

        Applies pre-flight sanitization, then retries once if the API returns
        a fixable 400 error. Unfixable errors get human-centered messages.

        Args:
            request: Anthropic Messages API request.

        Yields:
            Streaming events from the Anthropic SDK (includes text, thinking, etc.).
        """
        with tracer.start_as_current_span("anthropic.stream") as span:
            span.set_attribute("llm.model", request["model"])
            span.set_attribute("llm.stream", True)

            kwargs = self._prepare_request_kwargs(request)
            try:
                async with self._client.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        yield event
            except anthropic.BadRequestError as e:
                fixed_kwargs = _try_auto_fix(kwargs, e)
                if fixed_kwargs is not None:
                    logger.info("[auto-fix] %s — retrying stream", e.message)
                    span.set_attribute("luthien.auto_fix", True)
                    async with self._client.messages.stream(**fixed_kwargs) as stream:
                        async for event in stream:
                            yield event
                else:
                    raise _rewrite_bad_request_error(kwargs, e) from e
            except anthropic.InternalServerError as e:
                raise _rewrite_server_error(kwargs, e) from e


__all__ = ["AnthropicClient"]
