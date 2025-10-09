"""Policy that buffers tool-call chunks, logs them, then forwards output."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Optional

from luthien_proxy.types import JSONObject

from .base import LuthienPolicy, StreamPolicyContext

TOOL_CALL_DEBUG_TYPE = "conversation:tool-call"
TOOL_CALL_SCHEMA = "luthien.conversation.tool_call.v1"


@dataclass
class ToolCallState:
    """Incrementally collected state for a streaming tool call."""

    identifier: str
    call_type: str
    name: str = ""
    arguments: str = ""


@dataclass
class ToolCallBufferContext(StreamPolicyContext):
    """Stream context with tool call tracking and buffering."""

    content_parts: list[str] = field(default_factory=list)
    tool_calls: dict[str, ToolCallState] = field(default_factory=dict)
    tool_call_indexes: dict[int, str] = field(default_factory=dict)
    finish_reason: Optional[str] = None
    role: Optional[str] = None
    buffered_chunks: list[dict[str, Any]] = field(default_factory=list)
    tool_call_active: bool = False
    logged_tool_ids: set[str] = field(default_factory=set)


class ToolCallBufferPolicy(LuthienPolicy):
    """Intercept streaming tool calls, log them, then replay original chunks."""

    def create_stream_context(self, stream_id: str, request_data: dict[str, Any]) -> ToolCallBufferContext:
        """Initialise stream context with additional buffering state."""
        return ToolCallBufferContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: ToolCallBufferContext,
        incoming_stream: AsyncIterator[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Buffer tool-call chunks until completion, otherwise forward immediately."""
        try:
            async for chunk in incoming_stream:
                context.chunk_count += 1
                self._capture_stream_chunk(context, chunk)

                if self._buffer_tool_chunk(context, chunk):
                    flushed = await self._maybe_flush_tool_calls(context, chunk)
                    if flushed:
                        for buffered in flushed:
                            yield buffered
                    continue

                yield chunk
        finally:
            if context.tool_call_active and context.buffered_chunks:
                flushed = await self._flush_tool_calls(context)
                for buffered in flushed:
                    yield buffered

    async def async_post_call_success_hook(
        self,
        data: Mapping[str, Any],
        user_api_key_dict: Mapping[str, Any] | None,
        response: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Log non-stream tool calls."""
        if bool(data.get("stream")):
            return response

        tool_calls = self._extract_message_tool_calls(response)
        if tool_calls:
            record = self._build_log_record(
                call_id=self._require_call_id(data),
                trace_id=self._extract_trace_id(data),
                stream_id=None,
                chunks_buffered=None,
                tool_calls=tool_calls,
            )
            await self._record_debug_event(TOOL_CALL_DEBUG_TYPE, record)

        return response

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------
    def _buffer_tool_chunk(self, context: ToolCallBufferContext, chunk: Mapping[str, Any]) -> bool:
        choice = self._first_choice(chunk)
        if choice is None:
            if context.tool_call_active:
                context.buffered_chunks.append(dict(chunk))
                return True
            return False

        message = choice.get("message")
        if isinstance(message, Mapping) and self._message_contains_tool_call(message):
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                self._merge_message_tool_calls(context, tool_calls)
            legacy = message.get("function_call")
            if isinstance(legacy, Mapping):
                self._merge_legacy_function_delta(context, legacy)

        contains_tool_data = self._chunk_contains_tool_call(chunk)
        if context.tool_call_active or contains_tool_data:
            context.tool_call_active = True
            context.buffered_chunks.append(dict(chunk))
            return True

        return False

    async def _maybe_flush_tool_calls(
        self,
        context: ToolCallBufferContext,
        chunk: Mapping[str, Any],
    ) -> list[dict[str, Any]] | None:
        if not context.tool_call_active:
            return None

        choice = self._first_choice(chunk)
        if choice is None:
            return None

        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason == "tool_calls":
            return await self._flush_tool_calls(context)

        message = choice.get("message")
        if isinstance(message, Mapping):
            if self._message_contains_tool_call(message):
                return await self._flush_tool_calls(context)

        return None

    async def _flush_tool_calls(self, context: ToolCallBufferContext) -> list[dict[str, Any]]:
        call_id = self._require_call_id(context.original_request)
        new_states: list[ToolCallState] = []
        for identifier in sorted(context.tool_calls.keys()):
            state = context.tool_calls[identifier]
            if identifier not in context.logged_tool_ids:
                new_states.append(state)

        if not new_states:
            context.buffered_chunks.clear()
            context.tool_call_active = False
            return []

        payload = self._build_log_record(
            call_id=call_id,
            trace_id=self._extract_trace_id(context.original_request),
            stream_id=context.stream_id,
            chunks_buffered=len(context.buffered_chunks),
            tool_calls=[
                {
                    "id": state.identifier,
                    "type": state.call_type,
                    "name": state.name,
                    "arguments": state.arguments,
                }
                for state in new_states
            ],
        )
        await self._record_debug_event(TOOL_CALL_DEBUG_TYPE, payload)
        context.logged_tool_ids.update(state.identifier for state in new_states)

        chunk = self._build_buffered_tool_chunk(context, new_states)
        context.buffered_chunks.clear()
        context.tool_call_active = False
        return [chunk]

    def _build_buffered_tool_chunk(
        self,
        context: ToolCallBufferContext,
        states: list[ToolCallState],
    ) -> dict[str, Any]:
        base = context.buffered_chunks[-1] if context.buffered_chunks else {"choices": [{"index": 0}]}
        chunk = deepcopy(base)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            choices = [{"index": 0}]
            chunk["choices"] = choices
        raw_choice = choices[0]
        if isinstance(raw_choice, dict):
            choice: dict[str, Any] = raw_choice
        else:
            choice = {}
            choices[0] = choice
        choice.pop("message", None)
        choice.pop("logprobs", None)

        index_value = choice.get("index")
        if not isinstance(index_value, int):
            choice["index"] = 0

        delta = {
            "role": context.role or "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": state.identifier,
                    "type": state.call_type or "function",
                    "function": {
                        "name": state.name or "",
                        "arguments": state.arguments,
                    },
                }
                for state in states
            ],
        }
        choice["delta"] = delta
        choice["finish_reason"] = "tool_calls"
        return chunk

    def _merge_legacy_function_delta(
        self,
        context: ToolCallBufferContext,
        legacy: Mapping[str, Any],
    ) -> None:
        """Normalize legacy LiteLLM `function_call` payloads into tool-call state."""
        synthetic = {
            "id": legacy.get("id"),
            "type": "function",
            "function": legacy,
        }
        self._merge_message_tool_calls(context, [synthetic])

    def _chunk_contains_tool_call(self, chunk: Mapping[str, Any]) -> bool:
        choice = self._first_choice(chunk)
        if choice is None:
            return False

        delta = choice.get("delta")
        if isinstance(delta, Mapping):
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                return True
            function_call = delta.get("function_call")
            if isinstance(function_call, Mapping):
                return True

        message = choice.get("message")
        if isinstance(message, Mapping):
            if self._message_contains_tool_call(message):
                return True

        return False

    def _first_choice(self, chunk: Mapping[str, Any]) -> Mapping[str, Any] | None:
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        return first if isinstance(first, Mapping) else None

    # ------------------------------------------------------------------
    # Non-stream helper
    # ------------------------------------------------------------------
    def _extract_message_tool_calls(self, response: Mapping[str, Any]) -> list[dict[str, str]]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        first = choices[0]
        if not isinstance(first, Mapping):
            return []
        message = first.get("message")
        if not isinstance(message, Mapping):
            return []

        calls = self._parse_tool_calls(message.get("tool_calls"), allow_empty=True)
        if calls:
            return calls

        legacy_function = message.get("function_call")
        if legacy_function is not None:
            return [self._parse_legacy_function_call(legacy_function)]

        return []

    # ------------------------------------------------------------------
    # Shared logging helper
    # ------------------------------------------------------------------
    def _build_log_record(
        self,
        *,
        call_id: str,
        trace_id: str | None,
        stream_id: str | None,
        chunks_buffered: int | None,
        tool_calls: list[dict[str, str]],
    ) -> JSONObject:
        record: dict[str, Any] = {
            "schema": TOOL_CALL_SCHEMA,
            "call_id": call_id,
            "trace_id": trace_id,
            "timestamp": self._timestamp(),
            "tool_calls": tool_calls,
        }
        if stream_id is not None:
            record["stream_id"] = stream_id
        if chunks_buffered is not None:
            record["chunks_buffered"] = chunks_buffered
        return record

    def _message_contains_tool_call(self, message: Mapping[str, Any]) -> bool:
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return True
        function_call = message.get("function_call")
        return isinstance(function_call, Mapping)

    def _merge_message_tool_calls(
        self,
        context: ToolCallBufferContext,
        tool_calls: list[Mapping[str, Any]],
    ) -> None:
        for index, raw in enumerate(tool_calls):
            if not isinstance(raw, Mapping):
                continue
            identifier = self._resolve_tool_call_identifier(context, raw, index)
            state = context.tool_calls.get(identifier)
            if state is None:
                call_type = raw.get("type")
                if not isinstance(call_type, str) or not call_type:
                    call_type = "function"
                state = ToolCallState(identifier=identifier, call_type=call_type)
                context.tool_calls[identifier] = state
            else:
                state.identifier = identifier
            name = raw.get("name")
            if isinstance(name, str) and name:
                state.name = name
            arguments = raw.get("arguments")
            if isinstance(arguments, str) and arguments:
                state.arguments = arguments
            function_payload = raw.get("function")
            if isinstance(function_payload, Mapping):
                function_name = function_payload.get("name")
                if isinstance(function_name, str) and function_name:
                    state.name = function_name
                function_arguments = function_payload.get("arguments")
                if isinstance(function_arguments, str):
                    state.arguments = function_arguments

    # ------------------------------------------------------------------
    # Helper methods (extracted from removed ConversationLoggingPolicy)
    # ------------------------------------------------------------------
    def _capture_stream_chunk(
        self,
        context: ToolCallBufferContext,
        chunk: Mapping[str, Any],
    ) -> None:
        """Extract and update context state from a streaming chunk."""
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
            context.finish_reason = finish_reason
        delta = first.get("delta")
        if delta is None:
            return
        if not isinstance(delta, Mapping):
            raise TypeError("stream delta must be a mapping")
        role = delta.get("role")
        if role is not None:
            if not isinstance(role, str) or not role:
                raise ValueError("stream delta role must be a non-empty string")
            context.role = role
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise TypeError("stream delta content must be a string when present")
            context.content_parts.append(content)
        tool_calls = delta.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise TypeError("stream delta tool_calls must be a list")
            for index, raw in enumerate(tool_calls):
                if not isinstance(raw, Mapping):
                    raise TypeError("tool call delta entries must be mappings")
                identifier = self._resolve_tool_call_identifier(context, raw, index)
                state = context.tool_calls.get(identifier)
                if state is None:
                    call_type = raw.get("type")
                    if not isinstance(call_type, str) or not call_type:
                        call_type = "function"
                    call_id = raw.get("id")
                    state = ToolCallState(identifier=call_id or identifier, call_type=call_type)
                    context.tool_calls[identifier] = state
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
        context: ToolCallBufferContext,
        payload: Mapping[str, Any],
        fallback_index: int,
    ) -> str:
        """Resolve stable identifier for a tool call from streaming deltas."""
        call_index = payload.get("index")
        raw_identifier = payload.get("id")
        if isinstance(call_index, int):
            mapped_identifier = context.tool_call_indexes.get(call_index)
            if isinstance(raw_identifier, str) and raw_identifier:
                if mapped_identifier is not None and mapped_identifier != raw_identifier:
                    state = context.tool_calls.pop(mapped_identifier, None)
                    if state is not None:
                        state.identifier = raw_identifier
                        context.tool_calls[raw_identifier] = state
                context.tool_call_indexes[call_index] = raw_identifier
                return raw_identifier
            if mapped_identifier is not None:
                return mapped_identifier

        if isinstance(raw_identifier, str) and raw_identifier:
            return raw_identifier

        identifier = self._tool_call_identifier(payload, fallback_index)
        if isinstance(call_index, int):
            context.tool_call_indexes.setdefault(call_index, identifier)
        return identifier

    def _tool_call_identifier(self, payload: Mapping[str, Any], index: int) -> str:
        """Generate fallback identifier for a tool call."""
        identifier = payload.get("id")
        if isinstance(identifier, str) and identifier:
            return identifier
        idx = payload.get("index")
        if isinstance(idx, int):
            return f"tool_{idx}"
        return f"tool_{index}"

    def _parse_tool_calls(self, value: Any, *, allow_empty: bool = False) -> list[dict[str, str]]:
        """Parse tool_calls array from response message."""
        if value is None:
            return [] if allow_empty else self._unexpected_tool_call("value is None")
        if not isinstance(value, list):
            raise TypeError("tool_calls must be a list")
        parsed: list[dict[str, str]] = []
        for index, raw in enumerate(value):
            if not isinstance(raw, Mapping):
                raise TypeError("tool call entries must be mappings")
            call_type = raw.get("type")
            if not isinstance(call_type, str) or not call_type:
                call_type = "function"
            call_id = raw.get("id")
            if call_id is not None and not isinstance(call_id, str):
                raise TypeError("tool call id must be a string when present")
            function = raw.get("function")
            if not isinstance(function, Mapping):
                raise ValueError("tool call missing function payload")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("tool function name must be non-empty string")
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                raise TypeError("tool function arguments must be a string")
            parsed.append(
                {
                    "id": call_id or f"tool_{index}",
                    "type": call_type,
                    "name": name,
                    "arguments": arguments,
                }
            )
        return parsed

    def _parse_legacy_function_call(self, payload: Any) -> dict[str, str]:
        """Parse legacy function_call payload."""
        if not isinstance(payload, Mapping):
            raise TypeError("function_call payload must be a mapping")
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("function_call name must be a non-empty string")
        arguments = payload.get("arguments")
        if not isinstance(arguments, str):
            raise TypeError("function_call arguments must be a string")
        raw_id = payload.get("id")
        identifier = raw_id if isinstance(raw_id, str) and raw_id else "legacy_function_call"
        return {
            "id": identifier,
            "type": "function",
            "name": name,
            "arguments": arguments,
        }

    def _require_call_id(self, payload: Mapping[str, Any]) -> str:
        """Extract call_id from payload, raising error if not found."""
        candidates = (
            payload.get("litellm_call_id"),
            self._metadata_lookup(payload, "litellm_call_id"),
            self._params_lookup(payload, "litellm_call_id"),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
        raise ValueError("payload missing litellm_call_id")

    def _extract_trace_id(self, payload: Mapping[str, Any]) -> Optional[str]:
        """Extract trace_id from payload."""
        candidates = (
            payload.get("litellm_trace_id"),
            self._metadata_lookup(payload, "litellm_trace_id"),
            self._params_lookup(payload, "litellm_trace_id"),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    def _metadata_lookup(self, payload: Mapping[str, Any], key: str) -> Any:
        """Look up value in metadata dict."""
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            return metadata.get(key)
        return None

    def _params_lookup(self, payload: Mapping[str, Any], key: str) -> Any:
        """Look up value in litellm_params.metadata dict."""
        params = payload.get("litellm_params")
        if isinstance(params, Mapping):
            metadata = params.get("metadata")
            if isinstance(metadata, Mapping):
                return metadata.get(key)
        return None

    def _timestamp(self) -> str:
        """Return current UTC timestamp as ISO string."""
        return datetime.now(timezone.utc).isoformat()

    def _unexpected_tool_call(self, message: str) -> list[dict[str, str]]:
        """Raise error for unexpected tool call payload."""
        raise ValueError(f"unexpected tool call payload: {message}")


__all__ = ["ToolCallBufferPolicy"]
