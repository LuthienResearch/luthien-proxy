"""Type definitions for conversation history events.

Provides typing for event payloads and content structures stored in the database.

Note: These types intentionally overlap with llm/types/openai.py and llm/types/anthropic.py
but serve a different purpose. The llm/types/ modules define strict API types with literal
role types and required fields for making API calls. These types are looser variants designed
for parsing stored event data from the database, which may contain either OpenAI or Anthropic
format messages and needs lenient parsing (e.g., role as str instead of Literal["user"]).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NotRequired, Required, TypedDict

# =============================================================================
# Event Type Literals
# =============================================================================

TransactionEventType = Literal[
    "transaction.request_recorded",
    "transaction.non_streaming_response_recorded",
    "transaction.streaming_response_recorded",
]

# Policy event types follow pattern: policy.<policy_name>.<event_name>
# We don't enumerate all of them since policies can emit arbitrary events,
# but we recognize the prefix pattern.

# =============================================================================
# Request/Response Dict Structures
# =============================================================================


class RequestDict(TypedDict, total=False):
    """Structure of a request dict (OpenAI format)."""

    messages: list[MessageDict]
    model: str
    temperature: float | None
    max_tokens: int | None
    tools: list[dict[str, object]] | None
    tool_choice: str | dict[str, object] | None


class ResponseChoiceDict(TypedDict, total=False):
    """Structure of a choice within a response."""

    index: int
    message: MessageDict
    finish_reason: str | None


class ResponseDict(TypedDict, total=False):
    """Structure of a response dict (OpenAI format)."""

    id: str
    model: str
    choices: list[ResponseChoiceDict]
    usage: dict[str, int]


# =============================================================================
# Message and Content Block Structures
# =============================================================================


class MessageDict(TypedDict, total=False):
    """Structure of a message in a request/response.

    Supports both OpenAI and Anthropic formats.
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list[ContentBlockDict] | None
    name: str
    tool_calls: list[ToolCallDict]
    tool_call_id: str


class TextBlockDict(TypedDict):
    """Text content block."""

    type: Literal["text"]
    text: str


class ToolUseBlockDict(TypedDict):
    """Anthropic tool_use content block."""

    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, object]


class ToolResultBlockDict(TypedDict, total=False):
    """Anthropic tool_result content block."""

    type: Required[Literal["tool_result"]]
    tool_use_id: Required[str]
    content: str | list[ContentBlockDict]
    is_error: bool


class ImageBlockDict(TypedDict, total=False):
    """Image content block (OpenAI or Anthropic)."""

    type: Required[Literal["image", "image_url"]]
    image_url: dict[str, str]  # OpenAI
    source: dict[str, str]  # Anthropic


# Union of all content block types
ContentBlockDict = TextBlockDict | ToolUseBlockDict | ToolResultBlockDict | ImageBlockDict


class FunctionCallDict(TypedDict, total=False):
    """Structure of a function call within a tool call."""

    name: str
    arguments: str  # JSON string


class ToolCallDict(TypedDict, total=False):
    """Structure of a tool call (OpenAI format)."""

    id: str
    type: str  # "function"
    function: Required[FunctionCallDict]


# =============================================================================
# Event Payload Structures
# =============================================================================


class RequestRecordedPayload(TypedDict, total=False):
    """Payload for transaction.request_recorded events."""

    original_model: str
    final_model: str
    original_request: RequestDict
    final_request: RequestDict
    session_id: NotRequired[str | None]


class StreamingResponsePayload(TypedDict, total=False):
    """Payload for transaction.streaming_response_recorded events."""

    ingress_chunks: int
    egress_chunks: int
    original_response: ResponseDict
    final_response: ResponseDict
    session_id: NotRequired[str | None]


class NonStreamingResponsePayload(TypedDict, total=False):
    """Payload for transaction.non_streaming_response_recorded events."""

    original_finish_reason: str | None
    final_finish_reason: str | None
    original_response: ResponseDict
    final_response: ResponseDict
    session_id: NotRequired[str | None]


class PolicyEventPayload(TypedDict, total=False):
    """Common structure for policy event payloads.

    Policy events can have various fields depending on the policy.
    This captures the commonly used ones.
    """

    summary: str
    tool_name: str
    probability: float
    threshold: float
    explanation: str
    severity: str
    decision: str
    reason: str


# =============================================================================
# Stored Event Structure (from database)
# =============================================================================


class StoredEvent(TypedDict):
    """Structure of an event retrieved from the database."""

    event_type: str
    payload: dict[str, object]  # Will be narrowed based on event_type
    created_at: datetime  # asyncpg returns datetime for timestamp columns


# =============================================================================
# Type Aliases for Content
# =============================================================================

# Content can be a string or a list of content blocks
MessageContent = str | list[ContentBlockDict] | None


__all__ = [
    # Event types
    "TransactionEventType",
    # Request/Response structures
    "RequestDict",
    "ResponseDict",
    "ResponseChoiceDict",
    # Message structures
    "MessageDict",
    "ContentBlockDict",
    "TextBlockDict",
    "ToolUseBlockDict",
    "ToolResultBlockDict",
    "ImageBlockDict",
    "ToolCallDict",
    "FunctionCallDict",
    # Event payloads
    "RequestRecordedPayload",
    "StreamingResponsePayload",
    "NonStreamingResponsePayload",
    "PolicyEventPayload",
    # Stored event
    "StoredEvent",
    # Type aliases
    "MessageContent",
]
