"""Provider SDK typed-model parsers used at the passthrough materialization boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from google.genai import types as genai_types
from openai.types.chat import ChatCompletion
from openai.types.responses import (
    Response,
    ResponseOutputItem,
    ResponseOutputRefusal,
    ResponseOutputText,
)
from pydantic import TypeAdapter, ValidationError

from luthien_proxy.passthrough_materialize.payloads import JsonObject, JsonValue

_response_output_item_adapter = TypeAdapter(ResponseOutputItem)
_response_content_adapter = TypeAdapter(ResponseOutputText | ResponseOutputRefusal)


def parse_openai_chat_completion(raw: JsonObject) -> ChatCompletion:
    """Parse a captured Chat Completions response with the provider SDK model."""
    response = dict(raw)
    response.setdefault("id", "materialized_chat_completion")
    response.setdefault("model", "")
    response.setdefault("created", 0)
    response.setdefault("object", "chat.completion")
    response["choices"] = _chat_choices(raw.get("choices"))
    return ChatCompletion.model_validate(response)


def parse_openai_response(raw: JsonObject) -> Response:
    """Parse a captured Responses response after omitting unmodelled output variants."""
    response = dict(raw)
    response.setdefault("id", "materialized_response")
    response.setdefault("model", "")
    response.setdefault("created_at", 0)
    response.setdefault("object", "response")
    response.setdefault("parallel_tool_calls", True)
    response.setdefault("tool_choice", "auto")
    response.setdefault("tools", [])
    usage = response.get("usage")
    if isinstance(usage, Mapping):
        normalized_usage = dict(usage)
        input_details = normalized_usage.get("input_tokens_details")
        output_details = normalized_usage.get("output_tokens_details")
        normalized_usage["input_tokens_details"] = {
            "cache_write_tokens": 0,
            "cached_tokens": 0,
            **(dict(input_details) if isinstance(input_details, Mapping) else {}),
        }
        normalized_usage["output_tokens_details"] = {
            "reasoning_tokens": 0,
            **(dict(output_details) if isinstance(output_details, Mapping) else {}),
        }
        response["usage"] = normalized_usage
    response["output"] = _response_output(raw.get("output"))
    return Response.model_validate(response)


def parse_gemini_response(raw: JsonObject) -> genai_types.GenerateContentResponse:
    """Parse a Gemini generateContent payload with the provider SDK model."""
    response = dict(raw)
    candidates = response.get("candidates")
    if isinstance(candidates, Sequence) and not isinstance(candidates, str):
        response["candidates"] = [_gemini_candidate(candidate) for candidate in candidates]
    return genai_types.GenerateContentResponse.model_validate(response)


def _gemini_candidate(raw_candidate: JsonValue) -> JsonValue:
    if not isinstance(raw_candidate, Mapping):
        return raw_candidate
    candidate = dict(raw_candidate)
    content = candidate.get("content")
    if isinstance(content, Mapping):
        normalized_content = dict(content)
        parts = normalized_content.get("parts")
        if isinstance(parts, Sequence) and not isinstance(parts, str):
            normalized_content["parts"] = _gemini_parts(parts)
        candidate["content"] = normalized_content
    return candidate


def _gemini_parts(parts: Sequence[JsonValue]) -> list[JsonValue]:
    parsed: list[JsonValue] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        try:
            genai_types.Part.model_validate(part)
        except ValidationError:
            continue
        parsed.append(part)
    return parsed


def _chat_choices(raw_choices: JsonValue | None) -> list[dict[str, JsonValue]]:
    if not isinstance(raw_choices, Sequence) or isinstance(raw_choices, str):
        return []
    choices: list[dict[str, JsonValue]] = []
    for index, raw_choice in enumerate(raw_choices):
        if not isinstance(raw_choice, Mapping):
            continue
        choice = dict(raw_choice)
        choice.setdefault("index", index)
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {"stop", "length", "tool_calls", "content_filter", "function_call"}:
            choice["finish_reason"] = "stop"
        message = choice.get("message")
        if isinstance(message, Mapping):
            normalized_message = dict(message)
            tool_calls = normalized_message.get("tool_calls")
            if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str):
                normalized_message["tool_calls"] = [
                    {"id": "", **dict(call)} if isinstance(call, Mapping) else call for call in tool_calls
                ]
            choice["message"] = normalized_message
        choices.append(choice)
    return choices


def _response_output(raw_output: JsonValue | None) -> list[dict[str, JsonValue]]:
    if not isinstance(raw_output, Sequence) or isinstance(raw_output, str):
        return []
    output: list[dict[str, JsonValue]] = []
    for index, raw_item in enumerate(raw_output):
        if not isinstance(raw_item, Mapping):
            continue
        item = _response_item_defaults(raw_item, index)
        try:
            _response_output_item_adapter.validate_python(item)
        except ValidationError:
            continue
        output.append(item)
    return output


def _response_item_defaults(raw_item: Mapping[str, JsonValue], index: int) -> dict[str, JsonValue]:
    item = dict(raw_item)
    item.setdefault("id", f"materialized_output_{index}")
    item.setdefault("status", "completed")
    content = item.get("content")
    if isinstance(content, Sequence) and not isinstance(content, str):
        item["content"] = _response_content(content)
    return item


def _response_content(content: Sequence[JsonValue]) -> list[dict[str, JsonValue]]:
    parsed: list[dict[str, JsonValue]] = []
    for raw_part in content:
        if not isinstance(raw_part, Mapping):
            continue
        part = dict(raw_part)
        part.setdefault("annotations", [])
        try:
            _response_content_adapter.validate_python(part)
        except ValidationError:
            continue
        parsed.append(part)
    return parsed
