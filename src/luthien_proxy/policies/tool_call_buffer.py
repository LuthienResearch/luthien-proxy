"""Policy that buffers tool-call chunks, logs them, then forwards output."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

from luthien_proxy.types import JSONObject

from .conversation_logger import ConversationLoggingPolicy, ConversationLogStreamContext, ToolCallState

TOOL_CALL_DEBUG_TYPE = "conversation:tool-call"
TOOL_CALL_SCHEMA = "luthien.conversation.tool_call.v1"


@dataclass
class ToolCallBufferContext(ConversationLogStreamContext):
    """Extend conversation stream context with buffering metadata."""

    buffered_chunks: list[dict[str, Any]] = field(default_factory=list)
    tool_call_active: bool = False
    logged_tool_ids: set[str] = field(default_factory=set)


class ToolCallBufferPolicy(ConversationLoggingPolicy):
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
            await self._emit_stream_summary(context)

    async def async_post_call_success_hook(
        self,
        data: Mapping[str, Any],
        user_api_key_dict: Mapping[str, Any] | None,
        response: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Log non-stream tool calls in addition to base conversation logs."""
        result = await super().async_post_call_success_hook(data, user_api_key_dict, response)

        if bool(data.get("stream")):
            return result

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

        return result

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


__all__ = ["ToolCallBufferPolicy"]
