"""HTTP routes for receiving LiteLLM hook callbacks and trace queries."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable, Iterable, Literal, Optional, cast

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


class TraceInfo(BaseModel):
    """Summary row for a litellm_trace_id with aggregates."""

    trace_id: str
    call_count: int
    event_count: int
    latest: datetime


class ConversationEvent(BaseModel):
    """Normalized conversation event derived from debug hooks."""

    call_id: str
    trace_id: Optional[str] = None
    event_type: Literal[
        "request_started",
        "original_chunk",
        "final_chunk",
        "request_completed",
    ]
    sequence: int
    timestamp: datetime
    hook: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ConversationSnapshot(BaseModel):
    """Snapshot of a single call with its normalized events."""

    call_id: str
    trace_id: Optional[str] = None
    events: list[ConversationEvent] = Field(default_factory=list)


class TraceConversationSnapshot(BaseModel):
    """Snapshot of a trace spanning one or more calls."""

    trace_id: str
    call_ids: list[str] = Field(default_factory=list)
    events: list[ConversationEvent] = Field(default_factory=list)


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

        stored_record: dict[str, Any] = {"hook": hook_name, "payload": stored_payload}
        trace_id = _extract_trace_id(payload)
        if trace_id:
            record["litellm_trace_id"] = trace_id
            stored_record["litellm_trace_id"] = trace_id
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
            policy_payload = _strip_post_time_ns(payload)
            signature = inspect.signature(handler)
            parameters = signature.parameters
            accepts_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
            if accepts_var_kw:
                filtered_payload = policy_payload
            else:
                parameter_names = {name for name in parameters.keys() if name != "self"}
                filtered_payload = {k: v for k, v in policy_payload.items() if k in parameter_names}
            handler_result = await handler(**filtered_payload)
        final_result = handler_result if handler_result is not None else payload

        sanitized_result = _json_safe(final_result)

        result_record = {
            "hook": hook_name,
            "litellm_call_id": record.get("litellm_call_id"),
            "original": stored_payload,
            "result": sanitized_result,
        }
        if trace_id:
            result_record["litellm_trace_id"] = trace_id
        asyncio.create_task(debug_writer(f"hook_result:{hook_name}", result_record))

        call_id = result_record.get("litellm_call_id")
        if isinstance(call_id, str) and call_id:
            timestamp_dt = datetime.now(timezone.utc)
            events = _build_conversation_events(
                hook=hook_name,
                call_id=call_id,
                trace_id=trace_id,
                original=stored_payload,
                result=result_record["result"],
                timestamp_ns_fallback=time.time_ns(),
                timestamp=timestamp_dt,
            )
            for event in events:
                asyncio.create_task(_publish_conversation_event(redis_conn, event))
                asyncio.create_task(_publish_trace_conversation_event(redis_conn, event))

        return _strip_post_time_ns(final_result)
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
_CONVERSATION_TRACE_CHANNEL_PREFIX = "luthien:conversation-trace:"


def _conversation_channel(call_id: str) -> str:
    return f"{_CONVERSATION_CHANNEL_PREFIX}{call_id}"


def _conversation_trace_channel(trace_id: str) -> str:
    return f"{_CONVERSATION_TRACE_CHANNEL_PREFIX}{trace_id}"


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


def _extract_trace_id(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    request_data = payload.get("request_data")
    if isinstance(request_data, dict):
        trace_id = request_data.get("litellm_trace_id")
        if isinstance(trace_id, str) and trace_id:
            return trace_id
    data = payload.get("data")
    if isinstance(data, dict):
        trace_id = data.get("litellm_trace_id")
        if isinstance(trace_id, str) and trace_id:
            return trace_id
    return None


def _unwrap_response(payload: Any) -> Any:
    if payload is None:
        return None
    payload_dict = _require_dict(payload, "response envelope")
    for key in ("response", "response_obj", "raw_response"):
        if key in payload_dict:
            return payload_dict[key]
    return payload_dict


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


def _format_messages(messages: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    formatted: list[dict[str, str]] = []
    for role, content in messages:
        formatted.append({"role": role, "content": content})
    return formatted


def _extract_post_time_ns_from_any(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        candidate = value.get("post_time_ns")
        if isinstance(candidate, (int, float)):
            return int(candidate)
        for key in ("payload", "data", "request_data", "response", "response_obj", "raw_response", "chunk"):
            if key in value:
                nested = _extract_post_time_ns_from_any(value.get(key))
                if nested is not None:
                    return nested
        for nested_value in value.values():
            if isinstance(nested_value, (dict, list)):
                nested = _extract_post_time_ns_from_any(nested_value)
                if nested is not None:
                    return nested
    elif isinstance(value, list):
        for item in value:
            nested = _extract_post_time_ns_from_any(item)
            if nested is not None:
                return nested
    return None


def _derive_sequence_ns(fallback_ns: int, *candidates: Any) -> int:
    for candidate in candidates:
        ns = _extract_post_time_ns_from_any(candidate)
        if ns is not None:
            return ns
    return fallback_ns


def _strip_post_time_ns(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_post_time_ns(inner) for key, inner in value.items() if key != "post_time_ns"}
    if isinstance(value, list):
        return [_strip_post_time_ns(item) for item in value]
    return value


def _build_conversation_events(
    *,
    hook: str,
    call_id: Optional[str],
    trace_id: Optional[str],
    original: Any,
    result: Any,
    timestamp_ns_fallback: int,
    timestamp: datetime,
) -> list[ConversationEvent]:
    if not isinstance(call_id, str) or not call_id:
        return []

    effective_trace_id = trace_id
    if effective_trace_id is None and isinstance(original, dict):
        effective_trace_id = _extract_trace_id(original)
    if effective_trace_id is None and isinstance(result, dict):
        effective_trace_id = _extract_trace_id(result)

    sequence_ns = _derive_sequence_ns(timestamp_ns_fallback, original, result)
    events: list[ConversationEvent] = []

    if hook == "async_pre_call_hook":
        original_payload = _require_dict(original, "pre-call original payload")
        result_payload = _require_dict(result, "pre-call result payload") if result is not None else original_payload
        originals = _messages_from_payload(original_payload)
        finals = _messages_from_payload(result_payload)
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_started",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "original_messages": _format_messages(originals),
                    "final_messages": _format_messages(finals),
                    "raw_original": original_payload,
                    "raw_result": result_payload,
                },
            )
        )
        return events

    if hook == "async_post_call_streaming_iterator_hook":
        original_chunk = _extract_stream_chunk(original)
        final_chunk = _extract_stream_chunk(result)
        source_for_index = final_chunk if final_chunk is not None else original_chunk
        if source_for_index is None:
            return events
        try:
            choice_index = _extract_choice_index(source_for_index)
        except ValueError:
            choice_index = 0

        if original_chunk is not None:
            original_delta = _delta_from_chunk(original_chunk)
            events.append(
                ConversationEvent(
                    call_id=call_id,
                    trace_id=effective_trace_id,
                    event_type="original_chunk",
                    sequence=sequence_ns,
                    timestamp=timestamp,
                    hook=hook,
                    payload={
                        "delta": original_delta,
                        "choice_index": choice_index,
                        "raw_chunk": original_chunk,
                        "raw_payload": original,
                    },
                )
            )

        if final_chunk is not None:
            final_delta = _delta_from_chunk(final_chunk)
            events.append(
                ConversationEvent(
                    call_id=call_id,
                    trace_id=effective_trace_id,
                    event_type="final_chunk",
                    sequence=sequence_ns + 1,
                    timestamp=timestamp,
                    hook=hook,
                    payload={
                        "delta": final_delta,
                        "choice_index": choice_index,
                        "raw_chunk": final_chunk,
                        "raw_payload": result,
                    },
                )
            )
        return events

    if hook == "async_post_call_success_hook":
        try:
            original_response = _unwrap_response(original)
        except Exception:
            original_response = original
        try:
            final_response = _unwrap_response(result) if result is not None else None
        except Exception:
            final_response = result

        try:
            original_text = _extract_response_text(original_response)
        except Exception:
            original_text = ""
        try:
            final_text = _extract_response_text(final_response) if final_response is not None else ""
        except Exception:
            final_text = ""
        payload = {
            "status": "success",
            "original_response": original_text,
            "final_response": final_text or original_text,
            "raw_original": original_response,
            "raw_result": final_response,
        }
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_completed",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload=payload,
            )
        )
        return events

    if hook == "async_post_call_streaming_hook":
        summary_payload = result if result is not None else original
        summary_response = _unwrap_response(summary_payload)
        final_text = ""
        try:
            final_text = _extract_response_text(summary_response)
        except Exception:
            final_text = ""
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_completed",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "status": "stream_summary",
                    "final_response": final_text,
                    "raw_original": original,
                    "raw_result": result,
                },
            )
        )
        return events

    if hook == "async_post_call_failure_hook":
        events.append(
            ConversationEvent(
                call_id=call_id,
                trace_id=effective_trace_id,
                event_type="request_completed",
                sequence=sequence_ns,
                timestamp=timestamp,
                hook=hook,
                payload={
                    "status": "failure",
                    "raw_original": original,
                    "raw_result": result,
                },
            )
        )
        return events

    return events


async def _publish_conversation_event(
    redis: redis_client.RedisClient,
    event: ConversationEvent,
) -> None:
    if not event.call_id:
        return
    try:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - defensive serialization
        logger.error("Failed to serialize conversation event: %s", exc)
        return
    try:
        await redis.publish(_conversation_channel(event.call_id), payload)
    except Exception as exc:  # pragma: no cover - redis failures shouldn't break hooks
        logger.error("Failed to publish conversation event: %s", exc)


async def _publish_trace_conversation_event(
    redis: redis_client.RedisClient,
    event: ConversationEvent,
) -> None:
    trace_id = event.trace_id
    if not trace_id:
        return
    try:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - defensive serialization
        logger.error("Failed to serialize trace conversation event: %s", exc)
        return
    try:
        await redis.publish(_conversation_trace_channel(trace_id), payload)
    except Exception as exc:  # pragma: no cover - redis failures shouldn't break hooks
        logger.error("Failed to publish trace conversation event: %s", exc)


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


async def _conversation_sse_stream_by_trace(
    redis: redis_client.RedisClient,
    trace_id: str,
) -> AsyncGenerator[str, None]:
    channel = _conversation_trace_channel(trace_id)
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


async def _fetch_trace_entries_by_trace(
    trace_id: str,
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
                WHERE COALESCE(
                    jsonblob->>'litellm_trace_id',
                    jsonblob->'payload'->'request_data'->>'litellm_trace_id',
                    jsonblob->'payload'->'data'->>'litellm_trace_id'
                ) = $1
                ORDER BY time_created ASC
                """,
                trace_id,
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


def _events_from_trace_entry(entry: TraceEntry) -> list[ConversationEvent]:
    debug_type = entry.debug_type or ""
    if not debug_type.startswith("hook_result:"):
        return []

    hook = debug_type.split(":", 1)[1]
    payload = _require_dict(entry.payload, "trace entry payload")
    original = payload.get("original")
    result = payload.get("result")
    call_id = payload.get("litellm_call_id")
    trace_id = payload.get("litellm_trace_id")
    timestamp_ns = entry.post_time_ns if entry.post_time_ns is not None else int(entry.time.timestamp() * 1_000_000_000)
    timestamp = entry.time

    effective_result = result if result is not None else original
    return _build_conversation_events(
        hook=hook,
        call_id=call_id,
        trace_id=trace_id,
        original=original,
        result=effective_result,
        timestamp_ns_fallback=timestamp_ns,
        timestamp=timestamp,
    )


def _events_from_trace_entries(entries: Iterable[TraceEntry]) -> list[ConversationEvent]:
    collected: list[ConversationEvent] = []
    for entry in entries:
        collected.extend(_events_from_trace_entry(entry))
    collected.sort(key=lambda evt: (evt.sequence, evt.timestamp, evt.event_type))
    return collected


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


@router.get("/api/hooks/recent_traces", response_model=list[TraceInfo])
async def recent_traces(
    limit: int = Query(default=50, ge=1, le=500),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> list[TraceInfo]:
    """Return recent trace ids with call/event counts."""
    out: list[TraceInfo] = []
    if config.database_url is None or pool is None:
        return out
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT trace_id,
                       COUNT(*) AS event_count,
                       COUNT(DISTINCT call_id) AS call_count,
                       MAX(time_created) AS latest
                FROM (
                    SELECT COALESCE(
                               jsonblob->>'litellm_trace_id',
                               jsonblob->'payload'->'request_data'->>'litellm_trace_id',
                               jsonblob->'payload'->'data'->>'litellm_trace_id'
                           ) AS trace_id,
                           jsonblob->>'litellm_call_id' AS call_id,
                           time_created
                    FROM debug_logs
                ) AS traces
                WHERE trace_id IS NOT NULL
                GROUP BY trace_id
                ORDER BY latest DESC
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                trace_id = row["trace_id"]
                if not trace_id:
                    continue
                out.append(
                    TraceInfo(
                        trace_id=trace_id,
                        call_count=int(row["call_count"]),
                        event_count=int(row["event_count"]),
                        latest=row["latest"],
                    )
                )
    except Exception as exc:
        logger.error("Error fetching recent traces: %s", exc)
    return out


@router.get("/api/hooks/conversation", response_model=ConversationSnapshot)
async def conversation_snapshot(
    call_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> ConversationSnapshot:
    """Return normalized conversation events for a call ID."""
    entries = await _fetch_trace_entries(call_id, pool, config)
    events = _events_from_trace_entries(entries)
    trace_id = next((evt.trace_id for evt in events if evt.trace_id), None)
    return ConversationSnapshot(call_id=call_id, trace_id=trace_id, events=events)


@router.get("/api/hooks/conversation/stream")
async def conversation_stream(
    call_id: str = Query(..., min_length=4),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
) -> StreamingResponse:
    """Stream live conversation deltas for a call ID via SSE."""
    stream = _conversation_sse_stream(redis_conn, call_id)
    return StreamingResponse(stream, media_type="text/event-stream")


@router.get("/api/hooks/conversation/by_trace", response_model=TraceConversationSnapshot)
async def conversation_snapshot_by_trace(
    trace_id: str = Query(..., min_length=4),
    pool: Optional[db.DatabasePool] = Depends(get_database_pool),
    config: ProjectConfig = Depends(get_project_config),
) -> TraceConversationSnapshot:
    """Return normalized conversation events grouped by trace id."""
    entries = await _fetch_trace_entries_by_trace(trace_id, pool, config)
    events = _events_from_trace_entries(entries)
    call_ids = sorted({evt.call_id for evt in events if evt.call_id})
    return TraceConversationSnapshot(trace_id=trace_id, call_ids=call_ids, events=events)


@router.get("/api/hooks/conversation/stream_by_trace")
async def conversation_stream_by_trace(
    trace_id: str = Query(..., min_length=4),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
) -> StreamingResponse:
    """Stream live conversation deltas for a trace id via SSE."""
    stream = _conversation_sse_stream_by_trace(redis_conn, trace_id)
    return StreamingResponse(stream, media_type="text/event-stream")


__all__ = [
    "router",
    "get_hook_counters",
    "TraceEntry",
    "TraceResponse",
    "CallIdInfo",
    "TraceInfo",
    "ConversationEvent",
    "ConversationSnapshot",
    "TraceConversationSnapshot",
    "hook_generic",
    "trace_by_call_id",
    "recent_call_ids",
    "recent_traces",
    "conversation_snapshot",
    "conversation_stream",
    "conversation_snapshot_by_trace",
    "conversation_stream_by_trace",
]
