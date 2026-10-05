"""OpenAI Chat Completions passthrough normalizers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from openai.types.chat import ChatCompletionMessageFunctionToolCall, ChatCompletionMessageToolCallUnion
from pydantic import ValidationError

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind
from luthien_proxy.passthrough_materialize.openai_chat_stream import stream_chat_response
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    canonical_usage,
    error_response,
    fail,
    is_json_object,
    is_json_sequence,
    json_mutable_object,
    json_object_from_string,
    lenient_text_content_from_openai,
    optional_string,
    require_openai_endpoint,
    sequence_field,
    stop_reason,
)
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalRequestInput,
    CanonicalResponseInput,
    JsonMutableObject,
    JsonMutableValue,
    JsonObject,
    JsonValue,
)
from luthien_proxy.passthrough_materialize.provider_models import parse_openai_chat_completion


def normalize_openai_chat_request(
    endpoint: EligibleEndpoint, request: JsonObject, *, transaction_id: str | None = None
) -> CanonicalRequestInput:
    """Normalize a Chat Completions request into canonical request input."""
    require_openai_endpoint(endpoint, EndpointKind.OPENAI_CHAT_COMPLETIONS, transaction_id)
    model = optional_string(request, "model")
    messages = sequence_field(endpoint, request, "messages", transaction_id)
    final_request: JsonMutableObject = {"model": model, "messages": _chat_messages(endpoint, messages, transaction_id)}
    _copy_optional_request_fields(request, final_request)
    stream = request.get("stream") is True
    final_request["stream"] = stream
    return CanonicalRequestInput(
        endpoint=endpoint,
        is_streaming=stream,
        final_model=model,
        original_request=final_request,
        final_request=final_request,
        provider_request=request,
    )


def normalize_openai_chat_response(
    endpoint: EligibleEndpoint,
    response: JsonObject,
    *,
    request_is_streaming: bool,
    http_status: int,
    transaction_id: str | None = None,
) -> CanonicalResponseInput:
    """Normalize a Chat Completions response into canonical response input."""
    require_openai_endpoint(endpoint, EndpointKind.OPENAI_CHAT_COMPLETIONS, transaction_id)
    if http_status >= 400:
        final_response = error_response(http_status, response)
    elif request_is_streaming:
        final_response = stream_chat_response(endpoint, response, transaction_id)
    else:
        final_response = _buffered_chat_response(endpoint, response, transaction_id)
    model_value = final_response.get("model")
    final_model = model_value if isinstance(model_value, str) else None
    return CanonicalResponseInput(
        endpoint=endpoint,
        is_streaming=request_is_streaming,
        final_model=final_model,
        original_response=final_response,
        final_response=final_response,
        provider_response=response,
    )


def _chat_messages(
    endpoint: EligibleEndpoint, messages: JsonValue, transaction_id: str | None
) -> list[JsonMutableValue]:
    result: list[JsonMutableValue] = []
    if not is_json_sequence(messages):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "messages")
    for item in messages:
        if is_json_object(item):
            message = _chat_message(item)
            if message is not None:
                result.append(message)
    if not result:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "messages")
    return result


def _chat_message(item: Mapping[str, JsonValue]) -> JsonMutableObject | None:
    role = optional_string(item, "role")
    canonical_role = "system" if role == "developer" else role
    match canonical_role:
        case "system" | "user":
            content = lenient_text_content_from_openai(item.get("content"))
            if content is None:
                return None
            return {"role": canonical_role, "content": content}
        case "tool":
            content = lenient_text_content_from_openai(item.get("content"))
            tool_call_id = item.get("tool_call_id")
            if content is None or not isinstance(tool_call_id, str):
                return None
            return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        case "assistant":
            content_blocks: list[JsonMutableValue] = []
            normalized = lenient_text_content_from_openai(item.get("content"))
            match normalized:
                case str():
                    content_blocks.append({"type": "text", "text": normalized})
                case list():
                    content_blocks.extend(normalized)
                case None:
                    pass
            content_blocks.extend(_tool_calls(item.get("tool_calls")))
            return {"role": "assistant", "content": content_blocks} if content_blocks else None
        case _:
            return None


def _tool_calls(raw_calls: JsonValue) -> list[JsonMutableValue]:
    if not is_json_sequence(raw_calls):
        return []
    calls: list[JsonMutableValue] = []
    for raw_call in raw_calls:
        if not is_json_object(raw_call):
            continue
        function = raw_call.get("function")
        if not is_json_object(function):
            continue
        arguments = optional_string(function, "arguments") or "{}"
        if (call_id := optional_string(raw_call, "id")) is None or (name := optional_string(function, "name")) is None:
            continue
        try:
            arguments_value = json.loads(arguments)
        except json.JSONDecodeError:
            continue
        if not is_json_object(arguments_value):
            continue
        calls.append(
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": json_mutable_object(arguments_value),
            }
        )
    return calls


def _copy_optional_request_fields(request: JsonObject, final_request: JsonMutableObject) -> None:
    for key in ("tool_choice", "temperature", "top_p", "stop", "max_completion_tokens", "max_tokens"):
        if key in request:
            final_request[key] = json_mutable_object({"value": request[key]})["value"]
    if "max_tokens" not in final_request and isinstance(request.get("max_completion_tokens"), int):
        final_request["max_tokens"] = json_mutable_object({"value": request["max_completion_tokens"]})["value"]
    tools = request.get("tools")
    if tools is not None:
        final_request["tools"] = _tools(tools)


def _tools(tools: JsonValue) -> list[JsonMutableValue]:
    if not is_json_sequence(tools):
        return []
    result: list[JsonMutableValue] = []
    for tool in tools:
        if not is_json_object(tool) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not is_json_object(function):
            continue
        name = optional_string(function, "name")
        parameters = function.get("parameters")
        if name is None or not is_json_object(parameters):
            continue
        canonical_tool: JsonMutableObject = {
            "name": name,
            "input_schema": json_mutable_object(parameters),
        }
        description = optional_string(function, "description")
        if description is not None:
            canonical_tool["description"] = description
        result.append(canonical_tool)
    return result


def _buffered_chat_response(
    endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    try:
        parsed = parse_openai_chat_completion(response)
    except ValidationError:
        # The pinned openai SDK's strict Literals reject novel API values with an
        # uncaught ValidationError; convert to a typed, retryable failure so a
        # novel variant skips one transaction rather than wedging the batch.
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "response")
    if not parsed.choices:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "choices[0]")
    first = parsed.choices[0]
    message = first.message
    return _assistant_response(
        parsed.id,
        parsed.model,
        message.content,
        message.refusal,
        message.tool_calls,
        first.finish_reason,
        canonical_usage(parsed.usage.model_dump() if parsed.usage is not None else None),
        endpoint,
        transaction_id,
    )


def _assistant_response(
    response_id: str,
    model: str,
    text: str | None,
    refusal: str | None,
    tool_calls: Sequence[ChatCompletionMessageToolCallUnion] | None,
    finish_reason: str | None,
    usage: JsonMutableObject | None,
    endpoint: EligibleEndpoint,
    transaction_id: str | None,
) -> JsonMutableObject:
    content: list[JsonMutableValue] = []
    if text:
        content.append({"type": "text", "text": text})
    if refusal:
        content.append({"type": "text", "text": refusal})
    content.extend(_typed_tool_calls(endpoint, tool_calls, transaction_id))
    final: JsonMutableObject = {"role": "assistant", "content": content, "stop_reason": stop_reason(finish_reason)}
    final["id"] = response_id
    final["model"] = model
    if usage is not None:
        final["usage"] = usage
    return final


def _typed_tool_calls(
    endpoint: EligibleEndpoint,
    tool_calls: Sequence[ChatCompletionMessageToolCallUnion] | None,
    transaction_id: str | None,
) -> list[JsonMutableValue]:
    if tool_calls is None:
        return []
    calls: list[JsonMutableValue] = []
    for call in tool_calls:
        match call:
            case ChatCompletionMessageFunctionToolCall(id=call_id, function=function):
                if not call_id:
                    fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "tool_call.id")
                calls.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": function.name,
                        "input": json_object_from_string(endpoint, function.arguments, transaction_id),
                    }
                )
            case _:
                continue
    return calls
