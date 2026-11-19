"""OpenAI Responses passthrough normalizers."""

from __future__ import annotations

from collections.abc import Mapping

from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputItem,
    ResponseOutputMessage,
    ResponseOutputRefusal,
    ResponseOutputText,
)
from pydantic import ValidationError

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint, EndpointKind
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    canonical_usage,
    error_response,
    fail,
    is_json_object,
    is_json_sequence,
    json_mutable,
    json_mutable_object,
    json_object_from_string,
    lenient_text_content_from_openai,
    optional_string,
    require_openai_endpoint,
)
from luthien_proxy.passthrough_materialize.openai_responses_stream import fold_response_stream
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalRequestInput,
    CanonicalResponseInput,
    JsonMutableObject,
    JsonMutableValue,
    JsonObject,
    JsonValue,
)
from luthien_proxy.passthrough_materialize.provider_models import parse_openai_response


def normalize_openai_responses_request(
    endpoint: EligibleEndpoint, request: JsonObject, *, transaction_id: str | None = None
) -> CanonicalRequestInput:
    """Normalize an OpenAI Responses request into canonical request input."""
    require_openai_endpoint(endpoint, EndpointKind.OPENAI_RESPONSES, transaction_id)
    model = optional_string(request, "model")
    messages: list[JsonMutableValue] = []
    instructions = optional_string(request, "instructions")
    if instructions is not None:
        messages.append({"role": "system", "content": instructions})
    messages.extend(_input_messages(endpoint, request.get("input"), transaction_id))
    final_request: JsonMutableObject = {"model": model, "messages": messages}
    _copy_request_fields(request, final_request)
    stream = request.get("stream") is True
    final_request["stream"] = stream
    return CanonicalRequestInput(endpoint, stream, model, final_request, final_request, request)


def normalize_openai_responses_response(
    endpoint: EligibleEndpoint,
    response: JsonObject,
    *,
    request_is_streaming: bool,
    http_status: int,
    transaction_id: str | None = None,
) -> CanonicalResponseInput:
    """Normalize an OpenAI Responses response into canonical response input."""
    require_openai_endpoint(endpoint, EndpointKind.OPENAI_RESPONSES, transaction_id)
    if http_status >= 400:
        final_response = error_response(http_status, response)
    elif request_is_streaming:
        final_response = _stream_response(endpoint, response, transaction_id)
    else:
        final_response = _buffered_response(endpoint, response, transaction_id)
    model_value = final_response.get("model")
    final_model = model_value if isinstance(model_value, str) else None
    return CanonicalResponseInput(endpoint, request_is_streaming, final_model, final_response, final_response, response)


def _input_messages(
    endpoint: EligibleEndpoint, raw_input: JsonValue, transaction_id: str | None
) -> list[JsonMutableValue]:
    if isinstance(raw_input, str):
        return [{"role": "user", "content": raw_input}]
    if not is_json_sequence(raw_input):
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "input")
    result: list[JsonMutableValue] = []
    for item in raw_input:
        if is_json_object(item):
            message = _input_item(item)
            if message is not None:
                result.append(message)
    if not result:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "input")
    return result


def _input_item(item: Mapping[str, JsonValue]) -> JsonMutableValue | None:
    item_type = item.get("type")
    match item_type:
        case "function_call_output":
            call_id = optional_string(item, "call_id")
            output = optional_string(item, "output")
            if call_id is None or output is None:
                return None
            return {"role": "tool", "tool_call_id": call_id, "content": output}
        case None | "message":
            role = optional_string(item, "role")
            content = lenient_text_content_from_openai(item.get("content"))
            if role is None or content is None:
                return None
            return {"role": "system" if role == "developer" else role, "content": content}
        case _:
            return None


def _copy_request_fields(request: JsonObject, final_request: JsonMutableObject) -> None:
    for key in ("tool_choice", "temperature", "top_p", "max_output_tokens"):
        if key in request:
            final_request[key] = json_mutable(request[key])
    if isinstance(request.get("max_output_tokens"), int):
        final_request["max_tokens"] = json_mutable(request["max_output_tokens"])
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
        name = optional_string(tool, "name")
        parameters = tool.get("parameters")
        if name is None or not is_json_object(parameters):
            continue
        canonical: JsonMutableObject = {
            "name": name,
            "input_schema": json_mutable_object(parameters),
        }
        description = optional_string(tool, "description")
        if description is not None:
            canonical["description"] = description
        result.append(canonical)
    return result


def _buffered_response(
    endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None
) -> JsonMutableObject:
    try:
        parsed = parse_openai_response(response)
    except ValidationError:
        # The pinned openai SDK's strict Literals (e.g. Response.status) reject
        # novel API values with an uncaught ValidationError; convert to a typed,
        # retryable failure so a novel variant skips one transaction rather than
        # wedging the backfill batch.
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MALFORMED_PAYLOAD, "response")
    if not parsed.output:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "output")
    content = [block for item in parsed.output for block in _output_item(endpoint, item, transaction_id)]
    if not content:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "output content")
    usage = canonical_usage(parsed.usage.model_dump() if parsed.usage is not None else None)
    final = _response_base(parsed.id, parsed.model, usage, content)
    _copy_status_fields(response, parsed.status, final)
    return final


def _output_item(
    endpoint: EligibleEndpoint,
    item: ResponseOutputItem,
    transaction_id: str | None,
) -> list[JsonMutableValue]:
    match item:
        case ResponseOutputMessage(content=message_content):
            return [block for part in message_content for block in _output_content(part)]
        case ResponseFunctionToolCall(call_id=call_id, name=name, arguments=arguments):
            if not call_id:
                fail(
                    endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "function_call.call_id"
                )
            return [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": json_object_from_string(endpoint, arguments, transaction_id),
                }
            ]
        case _:
            return []


def _output_content(
    block: ResponseOutputText | ResponseOutputRefusal,
) -> list[JsonMutableValue]:
    match block:
        case ResponseOutputText(text=text):
            return [{"type": "text", "text": text}]
        case ResponseOutputRefusal(refusal=refusal):
            return [{"type": "text", "text": refusal}]
        case _:
            return []


def _stream_response(endpoint: EligibleEndpoint, response: JsonObject, transaction_id: str | None) -> JsonMutableObject:
    folded = fold_response_stream(endpoint, response, transaction_id)
    completed = folded.completed or {}
    if is_json_sequence(completed.get("output")):
        return _buffered_response(endpoint, completed, transaction_id)
    final = _response_base(
        optional_string(completed, "id"),
        optional_string(completed, "model"),
        canonical_usage(completed.get("usage")),
        [{"type": "text", "text": folded.text}],
    )
    _copy_status_fields(completed, optional_string(completed, "status"), final)
    return final


def _response_base(
    response_id: str | None, model: str | None, usage: JsonMutableObject | None, content: list[JsonMutableValue]
) -> JsonMutableObject:
    stop = (
        "tool_use" if any(is_json_object(item) and item.get("type") == "tool_use" for item in content) else "end_turn"
    )
    final: JsonMutableObject = {"role": "assistant", "content": content, "stop_reason": stop}
    if response_id is not None:
        final["id"] = response_id
    if model is not None:
        final["model"] = model
    if usage is not None:
        final["usage"] = usage
    return final


def _copy_status_fields(response: JsonObject, status: str | None, final: JsonMutableObject) -> None:
    for key in ("status", "incomplete_details", "error"):
        if key in response:
            final[key] = json_mutable(response[key])
    if status in ("failed", "incomplete") or response.get("error") is not None:
        final["stop_reason"] = "error"
