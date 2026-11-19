"""Capture helpers for multi-provider passthrough request_logs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
StreamFormat = Literal["openai-sse", "gemini-json-array", "gemini-sse"]

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_REQUEST_STRIPPED_HEADERS = _HOP_BY_HOP_HEADERS | {"host", "content-length"}
_RESPONSE_STRIPPED_HEADERS = _HOP_BY_HOP_HEADERS | {"content-encoding", "content-length"}


def build_passthrough_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Return client headers safe for upstream forwarding without server-key injection."""
    forwarded: dict[str, str] = {}
    for key, value in headers:
        lower_key = key.lower()
        if lower_key in _REQUEST_STRIPPED_HEADERS or lower_key.startswith("x-luthien-"):
            continue
        forwarded[key] = value
    return forwarded


def build_upstream_url(base_url: str, path: str, query: str) -> str:
    """Build an upstream URL from the configured base URL and client request target."""
    base = urlsplit(base_url.rstrip("/"))
    return urlunsplit((base.scheme, base.netloc, f"/{path}", query, ""))


def client_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return upstream headers safe to send to the passthrough client."""
    return {key: value for key, value in headers.items() if key.lower() not in _RESPONSE_STRIPPED_HEADERS}


def parse_openai_model(body: Mapping[str, JsonValue], override: str | None) -> str | None:
    """Return the request_logs model value for OpenAI passthrough calls."""
    if override:
        return override
    model = body.get("model")
    return model if isinstance(model, str) else None


def parse_gemini_model(path: str, body: Mapping[str, JsonValue], override: str | None) -> str | None:
    """Return the request_logs model value for Gemini passthrough calls."""
    if override:
        return override
    marker = "models/"
    if marker in path:
        model_path = path.split(marker, maxsplit=1)[1]
        return model_path.split(":", maxsplit=1)[0]
    model = body.get("model")
    return model if isinstance(model, str) else None


def reassemble_openai_sse_stream(chunks: Iterable[bytes]) -> JsonObject:
    """Stable wrapper: {"stream_format":"openai-sse","events":[...],"final":last_event_or_null}."""
    raw = _stream_text(chunks)
    events, complete = _decode_sse_events(raw, skip_done=True)
    final = events[-1] if events else None
    response: JsonObject = {"stream_format": "openai-sse", "events": events, "final": final}
    if not complete:
        response["raw"] = raw
    return response


def reassemble_gemini_json_array_stream(chunks: Iterable[bytes]) -> JsonObject:
    """Stable wrapper: {"stream_format":"gemini-json-array","chunks":[...],"final":null}."""
    raw = _stream_text(chunks)
    try:
        parsed = json_loads(raw)
    except ValueError:
        return _raw_stream_capture("gemini-json-array", [], raw)
    stream_chunks: list[JsonValue] = parsed if isinstance(parsed, list) else [parsed]
    return {"stream_format": "gemini-json-array", "chunks": stream_chunks, "final": None}


def reassemble_gemini_sse_stream(chunks: Iterable[bytes]) -> JsonObject:
    """Stable wrapper: {"stream_format":"gemini-sse","chunks":[...],"final":null}."""
    raw = _stream_text(chunks)
    stream_chunks, complete = _decode_sse_events(raw, skip_done=False)
    response: JsonObject = {"stream_format": "gemini-sse", "chunks": stream_chunks, "final": None}
    if not complete:
        response["raw"] = raw
    return response


def json_loads(raw: str | bytes) -> JsonValue:
    """Parse JSON into the passthrough capture JSON value type."""
    return json.loads(raw)


def _decode_sse_events(raw: str, *, skip_done: bool) -> tuple[list[JsonValue], bool]:
    payloads = _sse_data_payloads_from_text(raw)
    events: list[JsonValue] = []
    considered = 0
    for payload in payloads:
        if skip_done and payload == "[DONE]":
            continue
        considered += 1
        try:
            events.append(json_loads(payload))
        except ValueError:
            continue
    return events, len(events) == considered


def _sse_data_payloads_from_text(text: str) -> list[str]:
    payloads: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payloads.append(line.removeprefix("data:").strip())
    return payloads


def _stream_text(chunks: Iterable[bytes]) -> str:
    return b"".join(chunks).decode(errors="replace")


def _raw_stream_capture(stream_format: StreamFormat, chunks: list[JsonValue], raw: str) -> JsonObject:
    return {"stream_format": stream_format, "chunks": chunks, "raw": raw, "final": None}


__all__ = [
    "JsonObject",
    "JsonValue",
    "build_passthrough_headers",
    "build_upstream_url",
    "client_response_headers",
    "json_loads",
    "parse_gemini_model",
    "parse_openai_model",
    "reassemble_gemini_json_array_stream",
    "reassemble_gemini_sse_stream",
    "reassemble_openai_sse_stream",
]
