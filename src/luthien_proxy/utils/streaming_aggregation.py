"""ABOUTME: Utilities for aggregating streaming response chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class ToolCallState:
    """Incrementally collected state for a streaming tool call."""

    identifier: str
    call_type: str
    name: str = ""
    arguments: str = ""


@dataclass
class StreamChunkAggregator:
    """Aggregates streaming chunks into accumulated state.

    Tracks role, content, tool calls, and finish reason across chunks.
    """

    content_parts: list[str]
    tool_calls: dict[str, ToolCallState]
    tool_call_indexes: dict[int, str]
    finish_reason: str | None = None
    role: str | None = None

    def __init__(self):
        """Initialize aggregator with empty state."""
        self.content_parts = []
        self.tool_calls = {}
        self.tool_call_indexes = {}
        self.finish_reason = None
        self.role = None

    def capture_chunk(self, chunk: Mapping[str, Any]) -> None:
        """Process a chunk and update internal state."""
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        first = choices[0]
        if not isinstance(first, Mapping):
            raise TypeError("stream chunk choice must be a mapping")

        finish_reason = first.get("finish_reason")
        if finish_reason is not None:
            if not isinstance(finish_reason, str):
                raise TypeError("finish_reason must be a string when present")
            self.finish_reason = finish_reason

        delta = first.get("delta")
        if delta is None:
            return
        if not isinstance(delta, Mapping):
            raise TypeError("stream delta must be a mapping")

        role = delta.get("role")
        if role is not None:
            if not isinstance(role, str) or not role:
                raise ValueError("stream delta role must be a non-empty string")
            self.role = role

        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise TypeError("stream delta content must be a string when present")
            self.content_parts.append(content)

        tool_calls = delta.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise TypeError("stream delta tool_calls must be a list")
            for index, raw in enumerate(tool_calls):
                if not isinstance(raw, Mapping):
                    raise TypeError("tool call delta entries must be mappings")
                identifier = self._resolve_tool_call_identifier(raw, index)
                state = self.tool_calls.get(identifier)
                if state is None:
                    call_type = raw.get("type")
                    if not isinstance(call_type, str) or not call_type:
                        call_type = "function"
                    call_id = raw.get("id")
                    state = ToolCallState(identifier=call_id or identifier, call_type=call_type)
                    self.tool_calls[identifier] = state
                function_delta = raw.get("function")
                if function_delta is not None:
                    if not isinstance(function_delta, Mapping):
                        raise TypeError("tool call function delta must be a mapping")
                    name = function_delta.get("name")
                    if name is not None:
                        if not isinstance(name, str) or not name:
                            raise ValueError("tool function name must be non-empty string")
                        state.name = name
                    arguments = function_delta.get("arguments")
                    if arguments is not None:
                        if not isinstance(arguments, str):
                            raise TypeError("tool function arguments must be a string when present")
                        state.arguments += arguments

    def _resolve_tool_call_identifier(
        self,
        payload: Mapping[str, Any],
        fallback_index: int,
    ) -> str:
        """Resolve a unique identifier for a tool call from delta payload."""
        call_index = payload.get("index")
        raw_identifier = payload.get("id")
        if isinstance(call_index, int):
            mapped_identifier = self.tool_call_indexes.get(call_index)
            if isinstance(raw_identifier, str) and raw_identifier:
                if mapped_identifier is not None and mapped_identifier != raw_identifier:
                    state = self.tool_calls.pop(mapped_identifier, None)
                    if state is not None:
                        state.identifier = raw_identifier
                        self.tool_calls[raw_identifier] = state
                self.tool_call_indexes[call_index] = raw_identifier
                return raw_identifier
            if mapped_identifier is not None:
                return mapped_identifier

        if isinstance(raw_identifier, str) and raw_identifier:
            return raw_identifier

        identifier = _tool_call_identifier(payload, fallback_index)
        if isinstance(call_index, int):
            self.tool_call_indexes.setdefault(call_index, identifier)
        return identifier

    def get_accumulated_content(self) -> str:
        """Get the accumulated content text."""
        return "".join(self.content_parts)

    def get_tool_calls(self) -> list[dict[str, str]]:
        """Get the accumulated tool calls."""
        return [
            {
                "id": state.identifier,
                "type": state.call_type,
                "name": state.name,
                "arguments": state.arguments,
            }
            for state in self.tool_calls.values()
        ]


def _tool_call_identifier(payload: Mapping[str, Any], index: int) -> str:
    """Generate a fallback identifier for a tool call."""
    identifier = payload.get("id")
    if isinstance(identifier, str) and identifier:
        return identifier
    idx = payload.get("index")
    if isinstance(idx, int):
        return f"tool_{idx}"
    return f"tool_{index}"


__all__ = [
    "ToolCallState",
    "StreamChunkAggregator",
]
