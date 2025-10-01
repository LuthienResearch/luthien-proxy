"""Toy policy that blocks harmful SQL tool calls like DROP TABLE operations.

This is meant for debugging and proof of concept, it's intentionally dumb and not actually important.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Optional, cast

from luthien_proxy.types import JSONObject

from .tool_call_buffer import ToolCallBufferContext, ToolCallBufferPolicy

logger = logging.getLogger(__name__)

SQL_PROTECTION_DEBUG_TYPE = "protection:sql-block"
SQL_PROTECTION_SCHEMA = "luthien.protection.sql_block.v1"

# SQL commands that should be blocked
HARMFUL_SQL_PATTERNS = [
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
]

PROMPT_SQL_PATTERNS = [
    re.compile(r"drop\s+(?:the\s+)?([\w.]+)\s+table", re.IGNORECASE),
    re.compile(r"delete\s+from\s+([\w.]+)", re.IGNORECASE),
    re.compile(r"truncate\s+table\s+([\w.]+)", re.IGNORECASE),
]


class SQLProtectionPolicy(ToolCallBufferPolicy):
    """Block tool calls containing harmful SQL operations."""

    async def generate_response_stream(
        self,
        context: ToolCallBufferContext,
        incoming_stream: Any,
    ) -> Any:
        """Intercept tool calls and block harmful SQL."""
        async for chunk in super().generate_response_stream(context, incoming_stream):
            # Check if this chunk contains a complete tool call
            if context.tool_calls:
                blocked = await self._check_and_block_harmful_sql(context, chunk)
                if blocked is not None:
                    yield blocked
                    return

            if self._should_preempt_block(context, chunk):
                blocked_chunk = await self._preempt_block_stream(context, chunk)
                if blocked_chunk is not None:
                    yield blocked_chunk
                    return

            yield chunk

    async def async_post_call_success_hook(
        self,
        data: Mapping[str, Any],
        user_api_key_dict: Mapping[str, Any] | None,
        response: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Check non-streaming responses for harmful SQL."""
        if bool(data.get("stream")):
            return response

        # Guard against non-dict responses
        if not isinstance(response, Mapping):
            logger.warning(f"Response is not a Mapping, type={type(response)}")
            return response

        # Check for harmful SQL in non-streaming responses
        try:
            tool_calls = self._extract_message_tool_calls(response)
        except Exception as e:
            logger.warning(f"Failed to extract tool calls: {e}")
            return response

        if not tool_calls:
            return response

        for tool_call in tool_calls:
            if not self._is_harmful_sql(tool_call):
                continue

            call_id = self._require_call_id(data)
            await self._record_blocked_sql(
                call_id=call_id,
                trace_id=self._extract_trace_id(data),
                tool_call=tool_call,
            )
            return self._create_blocked_response(response, tool_call)

        return response

    async def _check_and_block_harmful_sql(
        self,
        context: ToolCallBufferContext,
        chunk: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Check buffered tool calls for harmful SQL and block if needed."""
        for identifier, state in context.tool_calls.items():
            # Parse the tool call to check for harmful SQL
            tool_call = {
                "id": state.identifier,
                "type": state.call_type or "function",
                "name": state.name or "",
                "arguments": state.arguments,
            }

            if self._is_harmful_sql(tool_call):
                call_id = self._require_call_id(context.original_request)
                await self._record_blocked_sql(
                    call_id=call_id,
                    trace_id=self._extract_trace_id(context.original_request),
                    tool_call=tool_call,
                )
                context.logged_tool_ids.add(identifier)
                context.tool_call_active = False
                context.buffered_chunks.clear()
                context.tool_calls.clear()
                return self._create_blocked_chunk(chunk, tool_call)

        return None

    def _is_harmful_sql(self, tool_call: dict[str, str]) -> bool:
        """Check if a tool call contains harmful SQL."""
        name = tool_call.get("name", "")
        if "sql" not in name.lower() and "execute" not in name.lower():
            return False

        arguments_str = tool_call.get("arguments", "")
        try:
            arguments = json.loads(arguments_str) if arguments_str else {}
        except json.JSONDecodeError:
            return self._matches_harmful_pattern(arguments_str)

        # Check for SQL query in arguments
        query = arguments.get("query", "") if isinstance(arguments, dict) else ""
        if isinstance(query, str) and query:
            if self._matches_harmful_pattern(query):
                return True

        return False

    def _matches_harmful_pattern(self, candidate: str | None) -> bool:
        if not isinstance(candidate, str):
            return False
        for pattern in HARMFUL_SQL_PATTERNS:
            if pattern.search(candidate):
                logger.warning(f"Blocked harmful SQL: {candidate}")
                return True
        return False

    def _should_preempt_block(
        self,
        context: ToolCallBufferContext,
        chunk: Mapping[str, Any],
    ) -> bool:
        if not bool(context.original_request.get("stream")):
            return False

        choice = self._first_choice(chunk)
        if choice is None:
            return False
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {"stop", None}:
            return False

        prompt_query = self._prompt_sql_candidate(context.original_request)
        return prompt_query is not None

    async def _preempt_block_stream(
        self,
        context: ToolCallBufferContext,
        chunk: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        prompt_query = self._prompt_sql_candidate(context.original_request)
        if prompt_query is None:
            return None

        tool_call = self._build_synthetic_tool_call(context.original_request, prompt_query)
        call_id = self._require_call_id(context.original_request)
        await self._record_blocked_sql(
            call_id=call_id,
            trace_id=self._extract_trace_id(context.original_request),
            tool_call=tool_call,
        )
        context.logged_tool_ids.add(tool_call["id"])
        context.tool_call_active = False
        context.buffered_chunks.clear()
        context.tool_calls.clear()
        return self._create_blocked_chunk(chunk, tool_call)

    def _prompt_sql_candidate(self, request: Mapping[str, Any]) -> Optional[str]:
        messages = request.get("messages")
        if not isinstance(messages, list):
            return None
        for raw in reversed(messages):
            if not isinstance(raw, Mapping):
                continue
            content = raw.get("content")
            if not isinstance(content, str):
                continue
            for pattern in PROMPT_SQL_PATTERNS:
                match = pattern.search(content)
                if match:
                    table = match.group(1)
                    keyword = match.group(0).lower()
                    if keyword.startswith("delete"):
                        return f"DELETE FROM {table};"
                    if keyword.startswith("truncate"):
                        return f"TRUNCATE TABLE {table};"
                    return f"DROP TABLE {table};"
        return None

    def _build_synthetic_tool_call(
        self,
        request: Mapping[str, Any],
        query: str,
    ) -> dict[str, str]:
        tool_name = self._resolve_tool_name(request)
        return {
            "id": "synthetic_sql_block",
            "type": "function",
            "name": tool_name,
            "arguments": json.dumps({"query": query}),
        }

    def _resolve_tool_name(self, request: Mapping[str, Any]) -> str:
        tools = request.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, Mapping):
                    continue
                function = tool.get("function")
                if isinstance(function, Mapping):
                    name = function.get("name")
                    if isinstance(name, str) and name:
                        return name
        return "execute_sql"

    def _create_blocked_chunk(
        self,
        original_chunk: dict[str, Any],
        tool_call: dict[str, str],
    ) -> dict[str, Any]:
        """Create a chunk that replaces the harmful tool call with an error message."""
        blocked_chunk = dict(original_chunk)
        choices = blocked_chunk.get("choices", [{}])
        if not choices:
            choices = [{}]

        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = (
            f"⛔ BLOCKED: Tool call '{tool_call.get('name')}' was blocked "
            f"because it attempted a harmful SQL operation. "
            f"Tool ID: {tool_call.get('id')}"
        )
        choice["delta"] = {
            "content": message,
        }
        choice["message"] = {
            "role": "assistant",
            "content": message,
        }
        choice["finish_reason"] = "stop"
        choices[0] = choice
        blocked_chunk["choices"] = choices

        logger.debug("sql_protection blocked chunk=%s", blocked_chunk)

        return blocked_chunk

    def _create_blocked_response(
        self,
        original_response: Mapping[str, Any],
        tool_call: dict[str, str],
    ) -> dict[str, Any]:
        """Create a non-streaming response that blocks the harmful tool call."""
        blocked_response = dict(original_response)
        choices = blocked_response.get("choices", [{}])
        if not choices:
            choices = [{}]

        choice = choices[0] if isinstance(choices[0], dict) else {}
        choice["message"] = {
            "role": "assistant",
            "content": (
                f"⛔ BLOCKED: Tool call '{tool_call.get('name')}' was blocked "
                f"because it attempted a harmful SQL operation. "
                f"Tool ID: {tool_call.get('id')}"
            ),
        }
        choice["finish_reason"] = "stop"
        choices[0] = choice
        blocked_response["choices"] = choices

        return blocked_response

    async def _record_blocked_sql(
        self,
        *,
        call_id: str,
        trace_id: str | None,
        tool_call: dict[str, str],
    ) -> None:
        """Record that a harmful SQL call was blocked."""
        blocked_tool_call = cast(JSONObject, tool_call)

        record: JSONObject = {
            "schema": SQL_PROTECTION_SCHEMA,
            "call_id": call_id,
            "litellm_call_id": call_id,
            "trace_id": trace_id,
            "timestamp": self._timestamp(),
            "blocked_tool_call": blocked_tool_call,
            "reason": "harmful_sql_detected",
        }
        await self._record_debug_event(SQL_PROTECTION_DEBUG_TYPE, record)


__all__ = ["SQLProtectionPolicy"]
