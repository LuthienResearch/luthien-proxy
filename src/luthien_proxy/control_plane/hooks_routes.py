"""HTTP routes for receiving LiteLLM hook callbacks and trace queries."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any, AsyncGenerator, Awaitable, Callable, Iterable, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from luthien_proxy.control_plane.utils.hooks import extract_call_id_for_hook
from luthien_proxy.control_plane.utils.streaming import extract_delta_text
from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.utils import db, redis_client
from luthien_proxy.utils.project_config import ProjectConfig

from .dependencies import (
    DebugLogWriter,
    get_active_policy,
    get_database_pool,
    get_debug_log_writer,
    get_hook_counter_state,
    get_project_config,
    get_redis_client,
)

router = APIRouter()

logger = logging.getLogger(__name__)


class TraceEntry(BaseModel):
    """A single hook event for a call ID, optionally with nanosecond time."""

    time: datetime
    post_time_ns: Optional[int] = None
    hook: Optional[str] = None
    debug_type: Optional[str] = None
    payload: dict[str, Any]


class TraceResponse(BaseModel):
    """Ordered list of hook entries belonging to a call ID."""

    call_id: str
    entries: list[TraceEntry]


class CallIdInfo(BaseModel):
    """Summary row for a recent litellm_call_id with counts and latest time."""

    call_id: str
    count: int
    latest: datetime


class ConversationMessage(BaseModel):
    """Single request-side message with original and final forms."""

    role: str
    original: str
    final: str


class ConversationChunk(BaseModel):
    """Streaming delta comparing original and final tokens."""

    sequence: int
    choice_index: int
    original_delta: str
    final_delta: str
    timestamp: datetime


class ConversationResponse(BaseModel):
    """Aggregated assistant response including per-chunk comparisons."""

    original_text: str = ""
    final_text: str = ""
    chunks: list[ConversationChunk] = Field(default_factory=list)
    completed: bool = False


class ConversationState(BaseModel):
    """Conversation snapshot reconstructed from debug hooks."""

    call_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
    response: ConversationResponse = Field(default_factory=ConversationResponse)


@router.get("/api/hooks/counters")
async def get_hook_counters(
    counters: Counter[str] = Depends(get_hook_counter_state),
) -> dict[str, int]:
    """Expose in-memory hook counters for sanity/testing scripts."""
    return dict(counters)


@router.post("/hooks/{hook_name}")
async def hook_generic(
    hook_name: str,
    payload: dict[str, Any],
    debug_writer: DebugLogWriter = Depends(get_debug_log_writer),
    policy: LuthienPolicy = Depends(get_active_policy),
    counters: Counter[str] = Depends(get_hook_counter_state),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
) -> Any:
    """Generic hook endpoint for any CustomLogger hook."""
    try:
        record_payload = _json_safe(payload)
        stored_payload = deepcopy(record_payload)
        record = {
            "hook": hook_name,
            "payload": record_payload,
        }
        logger.debug("hook=%s payload=%s", hook_name, json.dumps(record_payload, ensure_ascii=False))
        try:
            call_id = extract_call_id_for_hook(hook_name, payload)
            if isinstance(call_id, str) and call_id:
                record["litellm_call_id"] = call_id
        except Exception:
            pass
        stored_record = {"hook": hook_name, "payload": stored_payload}
        if "litellm_call_id" in record:
            stored_record["litellm_call_id"] = record["litellm_call_id"]
        asyncio.create_task(debug_writer(f"hook:{hook_name}", stored_record))
        name = hook_name.lower()
        counters[name] += 1
        handler = cast(
            Optional[Callable[..., Awaitable[Any]]],
            getattr(policy, name, None),
        )

        handler_result = None
        if handler:
            parameter_names = inspect.signature(handler).parameters.keys()
            filtered_payload = {k: v for k, v in payload.items() if k in parameter_names}
            handler_result = await handler(**filtered_payload)
        final_result = handler_result if handler_result is not None else payload

        result_record = {
            "hook": hook_name,
            "litellm_call_id": record.get("litellm_call_id"),
            "original": stored_payload,
            "result": _json_safe(final_result),
        }
        asyncio.create_task(debug_writer(f"hook_result:{hook_name}", result_record))

        call_id = result_record.get("litellm_call_id")
        if isinstance(call_id, str) and call_id:
            event = _build_conversation_event(
                hook_name,
                stored_payload,
                result_record["result"],
                timestamp=time.time(),
            )
            if event is not None:
                event["call_id"] = call_id
                asyncio.create_task(_publish_conversation_event(redis_conn, call_id, event))

        return final_result
    except Exception as exc:
        logger.error("hook_generic_error: %s", exc)
        raise HTTPException(status_code=500, detail=f"hook_generic_error: {exc}")


def _parse_jsonblob(raw: Any) -> dict[str, Any]:
    """Return a dict for a row's jsonblob without raising."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except Exception:
            return {"raw": raw}
    return {"raw": raw}


def _extract_post_ns(jb: dict[str, Any]) -> Optional[int]:
    payload = jb.get("payload")
    if not isinstance(payload, dict):
        return None
    ns = payload.get("post_time_ns")
    if isinstance(ns, int):
        return ns
    if isinstance(ns, float):
        return int(ns)
    return None


_CONVERSATION_CHANNEL_PREFIX = "luthien:conversation:"


def _conversation_channel(call_id: str) -> str:
    return f"{_CONVERSATION_CHANNEL_PREFIX}{call_id}"


def _require_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a dict; saw {type(value)!r}")
    return value


def _require_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list; saw {type(value)!r}")
    return value


def _require_str(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{context} must be a string; saw {type(value)!r}")
    return value


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        try:
            return repr(value)
        except Exception:
            return "<unserializable>"


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for index, item in enumerate(content):
            part = _require_dict(item, f"message content part #{index}")
            text = part.get("text")
            parts.append(_require_str(text, f"message content part #{index}.text"))
        return "".join(parts)
    if isinstance(content, dict):
        if "text" in content:
            return _require_str(content.get("text"), "message content text")
        inner = content.get("content")
        if inner is not None:
            return _message_content_to_text(inner)
    raise ValueError(f"Unexpected message content type: {type(content)!r}")


def _messages_from_payload(payload: Any) -> list[tuple[str, str]]:
    payload_dict = _require_dict(payload, "messages payload")
    container_key = "data" if "data" in payload_dict else "request_data"
    if container_key not in payload_dict:
        raise ValueError("messages payload missing 'data' or 'request_data'")
    request_dict = _require_dict(payload_dict[container_key], f"payload.{container_key}")
    messages = _require_list(request_dict.get("messages"), "payload messages")
    out: list[tuple[str, str]] = []
    for index, msg in enumerate(messages):
        msg_dict = _require_dict(msg, f"message entry #{index}")
        role = _require_str(msg_dict.get("role"), "message role")
        content = msg_dict.get("content")
        out.append((role, _message_content_to_text(content)))
    return out


def _extract_choice_index(chunk: Any) -> int:
    chunk_dict = _require_dict(chunk, "stream chunk")
    choices = _require_list(chunk_dict.get("choices"), "stream chunk choices")
    if not choices:
        raise ValueError("stream chunk choices list is empty")
    choice = _require_dict(choices[0], "stream chunk choice")
    idx = choice.get("index")
    if not isinstance(idx, int):
        raise ValueError("stream chunk choice missing integer index")
    return idx


def _delta_from_chunk(chunk: Any) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    chunk_dict = _require_dict(chunk, "stream chunk payload")
    return extract_delta_text(chunk_dict)


def _extract_stream_chunk(payload: Any) -> Any:
    if payload is None:
        return None
    payload_dict = _require_dict(payload, "stream chunk envelope")
    for key in ("response", "chunk", "response_obj", "raw_response"):
        if key in payload_dict:
            return payload_dict.get(key)
    return payload_dict


def _unwrap_response(payload: Any) -> Any:
    if payload is None:
        return None
    payload_dict = _require_dict(payload, "response envelope")
    for key in ("response", "response_obj", "raw_response"):
        if key in payload_dict:
            return payload_dict[key]
    return payload_dict


def _extract_stream_deltas(original: Any, result: Any | None) -> Optional[tuple[int, str, str]]:
    original_chunk = _extract_stream_chunk(original)
    final_chunk = _extract_stream_chunk(result) if result is not None else None
    source_for_index = original_chunk if original_chunk is not None else final_chunk
    if source_for_index is None:
        return None
    choice_index = _extract_choice_index(source_for_index)
    original_delta = _delta_from_chunk(original_chunk)
    final_delta = _delta_from_chunk(final_chunk) or original_delta
    if not original_delta and not final_delta:
        return None
    return choice_index, original_delta, final_delta


def _extract_response_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    response_dict = _require_dict(response, "response payload")
    if "choices" in response_dict:
        choices = _require_list(response_dict["choices"], "response choices")
        if not choices:
            raise ValueError("response choices list is empty")
        choice = _require_dict(choices[0], "response choice")
        if "message" in choice:
            message = _require_dict(choice["message"], "response choice.message")
            return _message_content_to_text(message.get("content"))
        if "delta" in choice:
            delta = _require_dict(choice["delta"], "response choice.delta")
            return _require_str(delta.get("content"), "response choice.delta.content")
    if "content" in response_dict:
        content = response_dict["content"]
        if isinstance(content, str):
            return content
    raise ValueError("Unrecognized response payload structure")


def _split_hook_payload(entry: TraceEntry) -> tuple[str, Any | None, Any | None]:
    payload = _require_dict(entry.payload, "trace entry payload")
    hook = _require_str(payload.get("hook"), "trace entry hook")
    if (entry.debug_type or "").startswith("hook_result:"):
        return hook, payload.get("original"), payload.get("result")
    return hook, payload.get("payload"), None


def _build_conversation_state(call_id: str, entries: Iterable[TraceEntry]) -> ConversationState:
    state = ConversationState(call_id=call_id)
    for entry in entries:
        hook, original, result = _split_hook_payload(entry)

        if hook == "async_pre_call_hook":
            original_payload = _require_dict(original, "pre-call original payload")
            result_payload = (
                _require_dict(result, "pre-call result payload") if result is not None else original_payload
            )
            originals = _messages_from_payload(original_payload)
            finals = _messages_from_payload(result_payload)
            max_len = max(len(originals), len(finals))
            messages: list[ConversationMessage] = []
            for idx in range(max_len):
                role = "unknown"
                original_text = ""
                final_text = ""
                if idx < len(originals):
                    role, original_text = originals[idx]
                if idx < len(finals):
                    final_role, final_text = finals[idx]
                    if final_role:
                        role = final_role
                messages.append(
                    ConversationMessage(
                        role=role,
                        original=original_text,
                        final=final_text or original_text,
                    )
                )
            state.messages = messages
            continue

        if hook == "async_post_call_streaming_iterator_hook":
            chunk_info = _extract_stream_deltas(original, result)
            if chunk_info is None:
                continue
            choice_index, original_delta, final_delta = chunk_info
            is_policy_result = (entry.debug_type or "").startswith("hook_result:")
            if is_policy_result and state.response.chunks:
                current_chunk = state.response.chunks[-1]
                previous_final = current_chunk.final_delta or ""
                if previous_final:
                    state.response.final_text = state.response.final_text[: -len(previous_final)]
                state.response.final_text += final_delta
                current_chunk.final_delta = final_delta
                current_chunk.choice_index = choice_index
                current_chunk.timestamp = entry.time
                continue

            chunk = ConversationChunk(
                sequence=len(state.response.chunks),
                choice_index=choice_index,
                original_delta=original_delta,
                final_delta=final_delta,
                timestamp=entry.time,
            )
            state.response.chunks.append(chunk)
            state.response.original_text += original_delta
            state.response.final_text += final_delta
            continue

        if hook == "async_post_call_success_hook":
            original_text = _extract_response_text(_unwrap_response(original))
            final_text = _extract_response_text(_unwrap_response(result)) if result is not None else ""
            if original_text:
                state.response.original_text = original_text
            if final_text:
                state.response.final_text = final_text
            if not state.response.final_text:
                state.response.final_text = state.response.original_text
            state.response.completed = True
            continue

        if hook == "async_post_call_streaming_hook":
            text = (
                _extract_response_text(_unwrap_response(result))
                if result is not None
                else _extract_response_text(_unwrap_response(original))
            )
            if text:
                state.response.final_text = text
                if not state.response.original_text:
                    state.response.original_text = text
            state.response.completed = True
            continue

    if not state.response.final_text:
        state.response.final_text = state.response.original_text
    return state


def _build_conversation_event(
    hook: str,
    original: Any,
    result: Any,
    *,
    timestamp: float,
) -> Optional[dict[str, Any]]:
    if hook == "async_pre_call_hook":
        original_payload = _require_dict(original, "pre-call original payload")
        result_payload = _require_dict(result, "pre-call result payload") if result is not None else original_payload
        originals = _messages_from_payload(original_payload)
        finals = _messages_from_payload(result_payload)
        items = []
        max_len = max(len(originals), len(finals))
        for idx in range(max_len):
            role = "unknown"
            original_text = ""
            final_text = ""
            if idx < len(originals):
                role, original_text = originals[idx]
            if idx < len(finals):
                candidate_role, final_text = finals[idx]
                if candidate_role:
                    role = candidate_role
            items.append(
                {
                    "role": role,
                    "original": original_text,
                    "final": final_text or original_text,
                }
            )
        return {"type": "request", "messages": items, "ts": timestamp}

    if hook == "async_post_call_streaming_iterator_hook":
        chunk_info = _extract_stream_deltas(original, result)
        if chunk_info is None:
            return None
        choice_index, original_delta, final_delta = chunk_info
        return {
            "type": "stream",
            "choice_index": choice_index,
            "original_delta": original_delta,
            "final_delta": final_delta,
            "replace": result is not None,
            "ts": timestamp,
        }

    if hook == "async_post_call_success_hook":
        original_resp = _unwrap_response(original)
        final_resp = _unwrap_response(result) if result is not None else None
        return {
            "type": "final",
            "original_text": _extract_response_text(original_resp),
            "final_text": _extract_response_text(final_resp) if final_resp is not None else "",
            "ts": timestamp,
        }

    return None


async def _publish_conversation_event(
    redis: redis_client.RedisClient,
    call_id: str,
    event: dict[str, Any],
) -> None:
    try:
        payload = json.dumps(event, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - defensive serialization
        logger.error("Failed to serialize conversation event: %s", exc)
        return
    try:
        await redis.publish(_conversation_channel(call_id), payload)
    except Exception as exc:  # pragma: no cover - redis failures shouldn't break hooks
        logger.error("Failed to publish conversation event: %s", exc)


async def _conversation_sse_stream(
    redis: redis_client.RedisClient,
    call_id: str,
) -> AsyncGenerator[str, None]:
    channel = _conversation_channel(call_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    heartbeat_interval = 15.0
    last_heartbeat = time.time()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            now = time.time()
            if message is None:
                if now - last_heartbeat >= heartbeat_interval:
                    last_heartbeat = now
                    yield ": ping\n\n"
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                text = data.decode("utf-8", errors="ignore")
            else:
                text = str(data)
            last_heartbeat = now
            yield f"data: {text}\n\n"
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.close()


async def _fetch_trace_entries(
    call_id: str,
    pool: Optional[db.DatabasePool],
    config: ProjectConfig,
) -> list[TraceEntry]:
    if config.database_url is None or pool is None:
        raise HTTPException(status_code=500, detail="DATABASE_URL is required for trace lookups")

    entries: list[TraceEntry] = []
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT time_created, debug_type_identifier, jsonblob
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' = $1
                ORDER BY time_created ASC
                """,
                call_id,
            )
            for row in rows:
                jb = _parse_jsonblob(row["jsonblob"])
                entries.append(
                    TraceEntry(
                        time=row["time_created"],
                        post_time_ns=_extract_post_ns(jb),
                        hook=jb.get("hook"),
                        debug_type=row["debug_type_identifier"],
                        payload=jb,
                    )
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace_error: {exc}")

    entries.sort(
        key=lambda e: (e.post_time_ns if e.post_time_ns is not None else int(e.time.timestamp() * 1_000_000_000))
    )
    return entries


@router.get("/api/hooks/trace_by_call_id", response_model=TraceResponse)
async def trace_by_call_id(
    call_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceResponse:
    """Return ordered hook entries from debug_logs for a litellm_call_id."""
    entries = await _fetch_trace_entries(call_id, pool, config)
    return TraceResponse(call_id=call_id, entries=entries)


@router.get("/api/hooks/recent_call_ids", response_model=list[CallIdInfo])
async def recent_call_ids(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[CallIdInfo]:
    """Return recent call IDs observed in debug logs with usage counts."""
    out: list[CallIdInfo] = []
    if config.database_url is None or pool is None:
        return out
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT jsonblob->>'litellm_call_id' as cid,
                       COUNT(*) as cnt,
                       MAX(time_created) as latest
                FROM debug_logs
                WHERE jsonblob->>'litellm_call_id' IS NOT NULL
                GROUP BY cid
                ORDER BY latest DESC
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                cid = row["cid"]
                if not cid:
                    continue
                out.append(CallIdInfo(call_id=cid, count=int(row["cnt"]), latest=row["latest"]))
    except Exception as exc:
        logger.error("Error fetching recent call ids: %s", exc)
    return out


@router.get("/api/hooks/conversation", response_model=ConversationState)
async def conversation_snapshot(
    call_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> ConversationState:
    """Return request/response comparison for a call ID."""
    entries = await _fetch_trace_entries(call_id, pool, config)
    return _build_conversation_state(call_id, entries)


@router.get("/api/hooks/conversation/stream")
async def conversation_stream(
    call_id: str = Query(..., min_length=4),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
) -> StreamingResponse:
    """Stream live conversation deltas for a call ID via SSE."""
    stream = _conversation_sse_stream(redis_conn, call_id)
    return StreamingResponse(stream, media_type="text/event-stream")


__all__ = [
    "router",
    "get_hook_counters",
    "TraceEntry",
    "TraceResponse",
    "CallIdInfo",
    "ConversationMessage",
    "ConversationChunk",
    "ConversationResponse",
    "ConversationState",
    "hook_generic",
    "trace_by_call_id",
    "recent_call_ids",
    "conversation_snapshot",
    "conversation_stream",
]
