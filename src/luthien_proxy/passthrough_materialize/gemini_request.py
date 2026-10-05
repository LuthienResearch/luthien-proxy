"""Gemini generateContent request normalization."""

from __future__ import annotations

from collections.abc import Mapping

from luthien_proxy.passthrough_materialize.endpoints import EligibleEndpoint
from luthien_proxy.passthrough_materialize.gemini_common import (
    gemini_endpoint_streaming,
    gemini_function_call_id,
    gemini_model_from_path,
)
from luthien_proxy.passthrough_materialize.openai_common import (
    PassthroughNormalizeReason,
    fail,
    is_json_object,
    is_json_sequence,
    json_mutable_object,
    optional_string,
    sequence_field,
)
from luthien_proxy.passthrough_materialize.payloads import (
    CanonicalRequestInput,
    JsonMutableObject,
    JsonMutableValue,
    JsonObject,
    JsonValue,
)


def normalize_gemini_request(
    endpoint: EligibleEndpoint, request: JsonObject, *, transaction_id: str | None = None
) -> CanonicalRequestInput:
    """Normalize a Gemini generateContent request into canonical request input."""
    stream = gemini_endpoint_streaming(endpoint, transaction_id)
    messages = _request_messages(endpoint, request, transaction_id)
    model = gemini_model_from_path(endpoint.path)
    final_request: JsonMutableObject = {"model": model, "messages": messages, "stream": stream}
    _copy_request_configuration(request, final_request)
    return CanonicalRequestInput(endpoint, stream, model, final_request, final_request, request)


def _request_messages(
    endpoint: EligibleEndpoint, request: JsonObject, transaction_id: str | None
) -> list[JsonMutableValue]:
    messages: list[JsonMutableValue] = []
    system_instruction = request.get("systemInstruction")
    if is_json_object(system_instruction):
        system_parts = _parts(system_instruction, "system")
        if system_parts:
            messages.append({"role": "system", "content": system_parts})
    contents = sequence_field(endpoint, request, "contents", transaction_id)
    for content in contents:
        if is_json_object(content):
            message = _content_message(content)
            if message is not None:
                messages.append(message)
    if not messages:
        fail(endpoint, transaction_id, PassthroughNormalizeReason.MISSING_REQUIRED_FIELD, "contents")
    return messages


def _content_message(content: JsonObject) -> JsonMutableObject | None:
    # Gemini API: role is OPTIONAL in contents[]; defaults to "user" when omitted.
    # https://ai.google.dev/api/generate-content#Content
    role = optional_string(content, "role") or "user"
    match role:
        case "user":
            parts = _parts(content, "user")
            return {"role": "user", "content": parts} if parts else None
        case "model":
            parts = _parts(content, "model")
            return {"role": "assistant", "content": parts} if parts else None
        case _:
            return None


def _parts(content: JsonObject, role: str) -> list[JsonMutableValue]:
    parts = content.get("parts")
    if not is_json_sequence(parts):
        return []
    blocks: list[JsonMutableValue] = []
    function_ordinal = 0
    for part in parts:
        if is_json_object(part):
            block = _part(part, role, function_ordinal)
            if block is not None:
                blocks.append(block)
            if "functionCall" in part or "functionResponse" in part:
                function_ordinal += 1
    return blocks


def _part(part: JsonObject, role: str, function_ordinal: int) -> JsonMutableValue | None:
    match part:
        case {"text": str() as text}:
            return {"type": "text", "text": text}
        case {"functionCall": Mapping() as call} if role == "model":
            return _function_call(call, function_ordinal)
        case {"functionResponse": Mapping() as response} if role == "user":
            return _function_response(response, function_ordinal)
        case {"functionCall": _} | {"functionResponse": _}:
            return None
        case _:
            return None


def _function_call(call: Mapping[str, JsonValue], ordinal: int) -> JsonMutableObject | None:
    name = optional_string(call, "name")
    args = call.get("args")
    if name is None or (args is not None and not is_json_object(args)):
        return None
    return {
        "type": "tool_use",
        "id": gemini_function_call_id(call, name, ordinal),
        "name": name,
        "input": {} if args is None else json_mutable_object(args),
    }


def _function_response(response: Mapping[str, JsonValue], ordinal: int) -> JsonMutableObject | None:
    name = optional_string(response, "name")
    response_value = response.get("response")
    if name is None or not is_json_object(response_value):
        return None
    return {
        "type": "tool_result",
        "tool_use_id": gemini_function_call_id(response, name, ordinal),
        "content": json_mutable_object(response_value),
    }


def _copy_request_configuration(
    request: JsonObject,
    final_request: JsonMutableObject,
) -> None:
    tools = request.get("tools")
    if tools is not None:
        final_request["tools"] = _tools(tools)
    tool_config = request.get("toolConfig")
    if tool_config is not None:
        tool_choice = _tool_choice(tool_config)
        if tool_choice is not None:
            final_request["tool_choice"] = tool_choice
    generation_config = request.get("generationConfig")
    if is_json_object(generation_config):
        final_request["generation_config"] = json_mutable_object(generation_config)
        _generation_config(generation_config, final_request)


def _tools(raw_tools: JsonValue) -> list[JsonMutableValue]:
    if not is_json_sequence(raw_tools):
        return []
    tools: list[JsonMutableValue] = []
    for tool in raw_tools:
        match tool:
            case {"functionDeclarations": _} if is_json_object(tool):
                declarations = tool.get("functionDeclarations")
                if not is_json_sequence(declarations):
                    continue
                for declaration in declarations:
                    if not is_json_object(declaration):
                        continue
                    normalized = _function_declaration(declaration)
                    if normalized is not None:
                        tools.append(normalized)
            case _:
                continue
    return tools


def _function_declaration(declaration: JsonObject) -> JsonMutableObject | None:
    name = optional_string(declaration, "name")
    if name is None:
        return None
    result: JsonMutableObject = {"name": name}
    description = optional_string(declaration, "description")
    if description is not None:
        result["description"] = description
    parameters = declaration.get("parameters")
    if is_json_object(parameters):
        result["input_schema"] = json_mutable_object(parameters)
    return result


def _tool_choice(raw_config: JsonValue) -> JsonMutableObject | None:
    if not is_json_object(raw_config):
        return None
    function_config = raw_config.get("functionCallingConfig")
    if not is_json_object(function_config):
        return None
    mode = optional_string(function_config, "mode")
    match mode:
        case "AUTO":
            result: JsonMutableObject = {"mode": "auto"}
        case "ANY":
            result = {"mode": "any"}
        case "NONE":
            result = {"mode": "none"}
        case "VALIDATED":
            result = {"mode": "validated"}
        case _:
            return None
    names = function_config.get("allowedFunctionNames")
    if is_json_sequence(names):
        allowed_names: list[JsonMutableValue] = []
        for name in names:
            if isinstance(name, str):
                allowed_names.append(name)
        result["allowed_function_names"] = allowed_names
    return result


def _generation_config(config: JsonObject, final_request: JsonMutableObject) -> None:
    for key, value in config.items():
        match key, value:
            case "temperature", int() | float() if not isinstance(value, bool):
                final_request["temperature"] = value
            case "topP", int() | float() if not isinstance(value, bool):
                final_request["top_p"] = value
            case "maxOutputTokens", int() if not isinstance(value, bool):
                final_request["max_tokens"] = value
            case "candidateCount", int() if not isinstance(value, bool):
                final_request["candidate_count"] = value
            case "stopSequences", _ if is_json_sequence(value):
                stop_sequences: list[JsonMutableValue] = []
                for item in value:
                    if isinstance(item, str):
                        stop_sequences.append(item)
                final_request["stop"] = stop_sequences
            case _:
                continue


__all__ = ["normalize_gemini_request"]
