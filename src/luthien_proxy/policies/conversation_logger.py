"""Policy that emits structured JSON logs for each request/response turn."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, Mapping, Optional, cast

from luthien_proxy.types import JSONObject

from .base import LuthienPolicy, StreamPolicyContext

logger = logging.getLogger("luthien.policy.conversation")


@dataclass
class ToolCallState:
    """Incrementally collected state for a streaming tool call."""

    identifier: str
    call_type: str
    name: str = ""
    arguments: str = ""


@dataclass
class ConversationLogStreamContext(StreamPolicyContext):
    """Track streaming state so we can emit a summary once the stream closes."""

    content_parts: list[str] = field(default_factory=list)
    tool_calls: dict[str, ToolCallState] = field(default_factory=dict)
    tool_call_indexes: dict[int, str] = field(default_factory=dict)
    finish_reason: Optional[str] = None
    role: Optional[str] = None


class ConversationLoggingPolicy(LuthienPolicy):
    """Emit structured logs that highlight tool-call turns."""

    schema = "luthien.conversation.turn.v1"

    async def async_pre_call_hook(
        self,
        data: Mapping[str, Any],
        user_api_key_dict: Mapping[str, Any] | None = None,
        cache: Mapping[str, Any] | None = None,
        call_type: str | None = None,
    ) -> None:
        """Record request metadata and message payload for later analysis.

        Note: call_id may not be available for all call types at pre-call time.
        For text_completion calls, LiteLLM generates the call_id after this hook.
        We skip recording in those cases - the post-call hook will capture everything.
        """
        call_id = self._extract_call_id(data)
        if not call_id:
            # Call ID not available yet - skip pre-call recording
            # The post-call hook will capture the full request/response
            logger.debug(f"Skipping pre-call recording for call_type={call_type} - no call_id available yet")
            return

        record = {
            "schema": self.schema,
            "call_id": call_id,
            "trace_id": self._extract_trace_id(data),
            "direction": "request",
            "timestamp": self._timestamp(),
            "model": data.get("model"),
            "call_type": call_type,
            "messages": list(self._format_messages(data.get("messages"))),
        }
        await self._record_turn(record)

    async def async_post_call_success_hook(
        self,
        data: Mapping[str, Any],
        user_api_key_dict: Mapping[str, Any] | None,
        response: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Log non-streaming responses while leaving payload unchanged."""
        if bool(data.get("stream")):
            return response
        call_id = self._require_call_id(data)
        summary = self._summarize_response(response)
        record = self._response_record(
            call_id=call_id,
            trace_id=self._extract_trace_id(data),
            summary=summary,
            chunks_seen=None,
        )
        await self._record_turn(record)
        return response

    def create_stream_context(self, stream_id: str, request_data: dict[str, Any]) -> ConversationLogStreamContext:
        """Initialise per-stream state used to aggregate chunk information."""
        return ConversationLogStreamContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: ConversationLogStreamContext,
        incoming_stream: AsyncIterator[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        """Mirror the upstream stream while collecting tool-call deltas."""
        try:
            async for chunk in incoming_stream:
                context.chunk_count += 1
                self._capture_stream_chunk(context, chunk)
                yield chunk
        finally:
            await self._emit_stream_summary(context)

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------
    def _format_messages(self, messages: Any) -> Iterable[dict[str, Any]]:
        if messages is None:
            return []
        if not isinstance(messages, list):
            raise TypeError("messages must be a list of dict objects")
        formatted: list[dict[str, Any]] = []
        for index, raw in enumerate(messages):
            if not isinstance(raw, Mapping):
                raise TypeError(f"message #{index} must be a mapping, saw {type(raw)!r}")
            role = raw.get("role")
            if not isinstance(role, str) or not role:
                raise ValueError(f"message #{index} missing role")
            message_type = raw.get("type")
            if message_type is not None and not isinstance(message_type, str):
                raise TypeError(f"message #{index} type must be a string when present")
            content = self._content_to_text(raw.get("content"))
            formatted.append(
                {
                    "index": index,
                    "role": role,
                    "type": message_type,
                    "content": content,
                }
            )
        return formatted

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def _summarize_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("response missing choices")
        choice0 = choices[0]
        if not isinstance(choice0, Mapping):
            raise TypeError("response choice must be a mapping")
        message = choice0.get("message")
        if not isinstance(message, Mapping):
            raise ValueError("response choice missing message")
        role = message.get("role")
        if not isinstance(role, str) or not role:
            role = "assistant"
        tool_calls = self._parse_tool_calls(message.get("tool_calls"), allow_empty=True)
        function_call = message.get("function_call")
        if function_call is not None:
            tool_calls = [self._parse_legacy_function_call(function_call)]
        content = self._content_to_text(message.get("content"))
        finish_reason = choice0.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError("finish_reason must be a string when present")
        response_type = "tool_call" if tool_calls else ("model" if content else "empty")
        return {
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "response_type": response_type,
            "finish_reason": finish_reason,
        }

    def _response_record(
        self,
        *,
        call_id: str,
        trace_id: Optional[str],
        summary: Mapping[str, Any],
        chunks_seen: Optional[int],
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema": self.schema,
            "call_id": call_id,
            "trace_id": trace_id,
            "direction": "response",
            "timestamp": self._timestamp(),
            "role": summary.get("role"),
            "content": summary.get("content"),
            "response_type": summary.get("response_type"),
            "tool_calls": summary.get("tool_calls"),
            "finish_reason": summary.get("finish_reason"),
        }
        if chunks_seen is not None:
            record["chunks_seen"] = chunks_seen
        return record

    async def _emit_stream_summary(self, context: ConversationLogStreamContext) -> None:
        summary = {
            "role": context.role or "assistant",
            "content": "".join(context.content_parts),
            "tool_calls": [
                {
                    "id": state.identifier,
                    "type": state.call_type,
                    "name": state.name,
                    "arguments": state.arguments,
                }
                for state in context.tool_calls.values()
            ],
            "response_type": "tool_call"
            if any(context.tool_calls.values())
            else ("model" if context.content_parts else "empty"),
            "finish_reason": context.finish_reason,
        }
        call_id = self._require_call_id(context.original_request)
        record = self._response_record(
            call_id=call_id,
            trace_id=self._extract_trace_id(context.original_request),
            summary=summary,
            chunks_seen=context.chunk_count,
        )
        await self._record_turn(record)

    def _capture_stream_chunk(
        self,
        context: ConversationLogStreamContext,
        chunk: Mapping[str, Any],
    ) -> None:
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

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _resolve_tool_call_identifier(
        self,
        context: ConversationLogStreamContext,
        payload: Mapping[str, Any],
        fallback_index: int,
    ) -> str:
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
        identifier = payload.get("id")
        if isinstance(identifier, str) and identifier:
            return identifier
        idx = payload.get("index")
        if isinstance(idx, int):
            return f"tool_{idx}"
        return f"tool_{index}"

    def _parse_tool_calls(self, value: Any, *, allow_empty: bool = False) -> list[dict[str, str]]:
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

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for index, item in enumerate(content):
                if not isinstance(item, Mapping):
                    raise TypeError("message content items must be mappings")
                item_type = item.get("type")
                if item_type != "text":
                    raise ValueError(f"unsupported content part type: {item_type!r}")
                text = item.get("text")
                if not isinstance(text, str):
                    raise TypeError("text content part must contain string text")
                parts.append(text)
            return "".join(parts)
        raise TypeError("unsupported message content structure")

    def _extract_call_id(self, payload: Mapping[str, Any]) -> Optional[str]:
        """Extract call_id from payload, returning None if not found."""
        candidates = (
            payload.get("litellm_call_id"),
            self._metadata_lookup(payload, "litellm_call_id"),
            self._params_lookup(payload, "litellm_call_id"),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    def _require_call_id(self, payload: Mapping[str, Any]) -> str:
        """Extract call_id from payload, raising error if not found."""
        call_id = self._extract_call_id(payload)
        if not call_id:
            raise ValueError("payload missing litellm_call_id")
        return call_id

    def _extract_trace_id(self, payload: Mapping[str, Any]) -> Optional[str]:
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
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            return metadata.get(key)
        return None

    def _params_lookup(self, payload: Mapping[str, Any], key: str) -> Any:
        params = payload.get("litellm_params")
        if isinstance(params, Mapping):
            metadata = params.get("metadata")
            if isinstance(metadata, Mapping):
                return metadata.get(key)
        return None

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _record_turn(self, record: Mapping[str, Any]) -> None:
        payload = cast(JSONObject, dict(record))
        await self._record_debug_event("conversation:turn", payload)
        logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))

    def _unexpected_tool_call(self, message: str) -> list[dict[str, str]]:
        raise ValueError(f"unexpected tool call payload: {message}")


__all__ = ["ConversationLoggingPolicy"]
