"""Persistence helpers for conversation events."""

from __future__ import annotations

import json
from typing import Iterable, Mapping, Sequence

from luthien_proxy.control_plane.conversation.models import ConversationEvent
from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db


async def record_conversation_events(
    pool: db.DatabasePool | None,
    events: Sequence[ConversationEvent],
) -> None:
    """Persist the given events into the structured conversation tables."""
    if pool is None or not events:
        return

    async with pool.connection() as conn:
        for event in events:
            await _ensure_call_row(conn, event)

            if event.event_type == "request_started":
                await _apply_request_started(conn, event)
            elif event.event_type == "request_completed":
                await _apply_request_completed(conn, event)

            await _insert_event_row(conn, event)

            if event.event_type == "final_chunk":
                await _record_tool_calls(conn, event)


async def _ensure_call_row(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    """Ensure a row exists for the call id, updating trace/updated_at as needed."""
    await conn.execute(
        """
        INSERT INTO conversation_calls (call_id, trace_id, created_at, updated_at)
        VALUES ($1, $2, $3, $3)
        ON CONFLICT (call_id) DO UPDATE
        SET trace_id = COALESCE(conversation_calls.trace_id, EXCLUDED.trace_id),
            updated_at = GREATEST(conversation_calls.updated_at, EXCLUDED.updated_at)
        """,
        event.call_id,
        event.trace_id,
        event.timestamp,
    )


async def _apply_request_started(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    payload = event.payload
    raw_original = payload.get("raw_original")

    model_name, provider = _extract_model_info(raw_original)
    metadata = _extract_request_metadata(payload)

    await conn.execute(
        """
        UPDATE conversation_calls
        SET trace_id = COALESCE(trace_id, $2),
            model_name = COALESCE(model_name, $3),
            provider = COALESCE(provider, $4),
            status = $5,
            metadata = CASE
                WHEN $6::jsonb IS NULL THEN metadata
                ELSE COALESCE(metadata, '{}'::jsonb) || $6::jsonb
            END,
            created_at = LEAST(created_at, $7),
            updated_at = $7
        WHERE call_id = $1
        """,
        event.call_id,
        event.trace_id,
        model_name,
        provider,
        "streaming",
        json.dumps(metadata) if metadata else None,
        event.timestamp,
    )


async def _apply_request_completed(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    payload = event.payload
    status = str(payload.get("status") or "success")

    metadata: dict[str, object] = {}
    original_response = payload.get("original_response")
    if isinstance(original_response, str) and original_response:
        metadata["original_response"] = original_response
    final_response = payload.get("final_response")
    if isinstance(final_response, str) and final_response:
        metadata["final_response"] = final_response
    request_messages = payload.get("request_messages")
    if isinstance(request_messages, list):
        metadata["request_messages"] = request_messages

    await conn.execute(
        """
        UPDATE conversation_calls
        SET trace_id = COALESCE(trace_id, $2),
            status = $3,
            completed_at = COALESCE($4, completed_at),
            metadata = CASE
                WHEN $5::jsonb IS NULL THEN metadata
                ELSE COALESCE(metadata, '{}'::jsonb) || $5::jsonb
            END,
            updated_at = COALESCE($4, updated_at)
        WHERE call_id = $1
        """,
        event.call_id,
        event.trace_id,
        status,
        event.timestamp,
        json.dumps(metadata) if metadata else None,
    )


async def _insert_event_row(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    payload = event.payload
    chunk_index = _as_int(payload.get("chunk_index"))
    choice_index = _as_int(payload.get("choice_index"))
    delta_text = _extract_delta_text(payload)
    role = _extract_role(payload)
    raw_chunk = payload.get("raw_chunk")

    await conn.execute(
        """
        INSERT INTO conversation_events (
            call_id,
            trace_id,
            event_type,
            hook,
            sequence,
            chunk_index,
            choice_index,
            role,
            delta_text,
            raw_chunk,
            payload,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        event.call_id,
        event.trace_id,
        event.event_type,
        event.hook,
        int(event.sequence),
        chunk_index,
        choice_index,
        role,
        delta_text,
        raw_chunk,
        payload,
        event.timestamp,
    )


async def _record_tool_calls(conn: db.ConnectionProtocol, event: ConversationEvent) -> None:
    tool_calls = _extract_tool_calls(event.payload)
    if not tool_calls:
        return

    chunk_index = _as_int(event.payload.get("chunk_index"))
    chunks_buffered = chunk_index + 1 if chunk_index is not None else None

    for tool_call in tool_calls:
        tool_call_id = tool_call.get("id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            continue

        name, arguments = _extract_tool_call_details(tool_call)
        await conn.execute(
            """
            INSERT INTO conversation_tool_calls (
                call_id,
                trace_id,
                tool_call_id,
                name,
                arguments,
                status,
                response,
                chunks_buffered,
                created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (call_id, tool_call_id) DO NOTHING
            """,
            event.call_id,
            event.trace_id,
            tool_call_id,
            name,
            arguments,
            "emitted",
            None,
            chunks_buffered,
            event.timestamp,
        )


def _extract_model_info(raw_original: object) -> tuple[str | None, str | None]:
    if not isinstance(raw_original, Mapping):
        return None, None

    model_name = None
    provider = None

    data = raw_original.get("data")
    if isinstance(data, Mapping):
        model_candidate = data.get("model")
        if isinstance(model_candidate, str):
            model_name = model_candidate
        metadata = data.get("metadata")
        if isinstance(metadata, Mapping):
            provider_candidate = metadata.get("deployment")
            if isinstance(provider_candidate, str):
                provider = provider_candidate

    deployment = raw_original.get("deployment")
    if isinstance(deployment, Mapping):
        params = deployment.get("litellm_params")
        if isinstance(params, Mapping):
            model_param = params.get("model")
            if isinstance(model_param, str):
                provider = provider or model_param

    return model_name, provider


def _extract_request_metadata(payload: JSONObject) -> dict[str, object]:
    metadata: dict[str, object] = {}
    original_messages = payload.get("original_messages")
    if isinstance(original_messages, list):
        metadata["original_messages"] = original_messages
    final_messages = payload.get("final_messages")
    if isinstance(final_messages, list):
        metadata["final_messages"] = final_messages
    return metadata


def _extract_delta_text(payload: JSONObject) -> str | None:
    delta = payload.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, Mapping):
        text = delta.get("content")
        if isinstance(text, str):
            return text
    return None


def _extract_role(payload: JSONObject) -> str | None:
    raw_chunk = payload.get("raw_chunk")
    if not isinstance(raw_chunk, Mapping):
        return None
    choices = raw_chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return None
    delta = choice.get("delta")
    if not isinstance(delta, Mapping):
        return None
    role = delta.get("role")
    return role if isinstance(role, str) else None


def _extract_tool_calls(payload: JSONObject) -> list[Mapping[str, object]]:
    tool_calls: list[Mapping[str, object]] = []
    candidates: list[object] = []

    raw_chunk = payload.get("raw_chunk")
    if isinstance(raw_chunk, Mapping):
        candidates.append(raw_chunk)

    raw_payload = payload.get("raw_payload")
    if isinstance(raw_payload, Mapping):
        response = raw_payload.get("response")
        if isinstance(response, Mapping):
            candidates.append(response)

    for candidate in candidates:
        choices = candidate.get("choices") if isinstance(candidate, Mapping) else None
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                continue
            tc_value = delta.get("tool_calls")
            if not isinstance(tc_value, Iterable):
                continue
            for item in tc_value:
                if isinstance(item, Mapping):
                    tool_calls.append(item)
    return tool_calls


def _extract_tool_call_details(tool_call: Mapping[str, object]) -> tuple[str | None, object | None]:
    name = None
    arguments: object | None = None

    function_payload = tool_call.get("function")
    if isinstance(function_payload, Mapping):
        func_name = function_payload.get("name")
        if isinstance(func_name, str):
            name = func_name
        raw_arguments = function_payload.get("arguments")
        if isinstance(raw_arguments, str):
            arguments = _safe_json_loads(raw_arguments)
        elif raw_arguments is not None:
            arguments = raw_arguments
    else:
        raw_name = tool_call.get("name")
        if isinstance(raw_name, str):
            name = raw_name
        raw_arguments = tool_call.get("arguments")
        if isinstance(raw_arguments, str):
            arguments = _safe_json_loads(raw_arguments)
        elif raw_arguments is not None:
            arguments = raw_arguments

    return name, arguments


def _safe_json_loads(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _as_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
