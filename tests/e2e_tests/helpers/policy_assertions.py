"""ABOUTME: Shared helpers for policy-specific e2e tests.
ABOUTME: Provides request execution, response validation, and trace assertion utilities.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from .infra import E2ESettings

try:
    from .policy_test_models import RequestSpec, ResponseAssertion
except ImportError:
    # For backward compatibility with existing tests
    RequestSpec = None  # type: ignore
    ResponseAssertion = None  # type: ignore


def build_policy_payload(settings: E2ESettings, *, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.model_name,
        "scenario": settings.scenario,
        "litellm_trace_id": f"e2e-test-{uuid.uuid4()}",
        "messages": [
            {
                "role": "user",
                "content": "I need to drop the customers table. It is critical.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "execute_sql",
                    "description": "Execute a SQL query on the database",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The SQL query to execute",
                            }
                        },
                        "required": ["query"],
                    },
                },
            }
        ],
    }
    if stream:
        payload["stream"] = True
    return payload


async def stream_policy_block(
    settings: E2ESettings,
    headers: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    payload = build_policy_payload(settings, stream=True)
    chunks: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            call_id = response.headers.get("x-litellm-call-id")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                if settings.verbose:
                    print(f"[e2e] streaming chunk: {json.dumps(chunk)}")
                if call_id is None:
                    call_id = chunk.get("id")
                chunks.append(chunk)
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                finish_reason = choice.get("finish_reason")
                if isinstance(content, str) and "BLOCKED" in content and finish_reason == "stop":
                    break
        if call_id is None:
            raise AssertionError("Streaming response missing call id")
        return call_id, chunks


# ==============================================================================
# Parameterized Test Execution Helpers
# ==============================================================================


def build_request_payload(
    settings: E2ESettings,
    request_spec: Any,  # RequestSpec type
    stream: bool,
) -> dict[str, Any]:
    """Build a request payload from a RequestSpec."""
    payload: dict[str, Any] = {
        "model": settings.model_name,
        "scenario": request_spec.scenario or settings.scenario,
        "litellm_trace_id": f"e2e-test-{uuid.uuid4()}",
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
            }
            for msg in request_spec.messages
        ],
    }

    if request_spec.tools:
        payload["tools"] = list(request_spec.tools)

    if stream:
        payload["stream"] = True

    if request_spec.extra_params:
        payload.update(request_spec.extra_params)

    return payload


async def execute_non_streaming_request(
    settings: E2ESettings,
    request_spec: Any,  # RequestSpec type
) -> tuple[dict[str, Any], str]:
    """Execute a non-streaming request and return (response_body, call_id)."""
    payload = build_request_payload(settings, request_spec, stream=False)
    headers = {
        "Authorization": f"Bearer {settings.master_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.post(
            f"{settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    assert response.status_code == 200, response.text
    body = response.json()

    call_id = response.headers.get("x-litellm-call-id") or body.get("id")
    if not call_id:
        call_id = response.headers.get("litellm-call-id")
    assert call_id, "Expected litellm call id in headers or body"

    return body, call_id


async def execute_streaming_request(
    settings: E2ESettings,
    request_spec: Any,  # RequestSpec type
) -> tuple[list[dict[str, Any]], str]:
    """Execute a streaming request and return (chunks, call_id)."""
    payload = build_request_payload(settings, request_spec, stream=True)
    headers = {
        "Authorization": f"Bearer {settings.master_key}",
        "Content-Type": "application/json",
    }

    chunks: list[dict[str, Any]] = []
    call_id = None

    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        async with client.stream(
            "POST",
            f"{settings.proxy_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            assert response.status_code == 200
            call_id = response.headers.get("x-litellm-call-id") or response.headers.get("litellm-call-id")

            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                if call_id is None:
                    call_id = chunk.get("id")
                chunks.append(chunk)

    assert call_id, "Streaming response missing call id"
    return chunks, call_id


def extract_message_content(response_body: dict[str, Any]) -> str:
    """Extract message content from a non-streaming response."""
    choices = response_body.get("choices", [])
    if not choices:
        return ""
    choice = choices[0]
    message = choice.get("message", {})
    return message.get("content", "")


def extract_streaming_content(chunks: list[dict[str, Any]]) -> str:
    """Extract accumulated content from streaming chunks."""
    content_parts = []
    for chunk in chunks:
        choices = chunk.get("choices", [])
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta", {})
        content = delta.get("content")
        if content:
            content_parts.append(content)
    return "".join(content_parts)


def extract_finish_reason(response_or_chunks: dict[str, Any] | list[dict[str, Any]]) -> str | None:
    """Extract finish_reason from response or chunks."""
    if isinstance(response_or_chunks, dict):
        # Non-streaming
        choices = response_or_chunks.get("choices", [])
        if choices:
            return choices[0].get("finish_reason")
    else:
        # Streaming - get last chunk's finish_reason
        for chunk in reversed(response_or_chunks):
            choices = chunk.get("choices", [])
            if choices:
                finish_reason = choices[0].get("finish_reason")
                if finish_reason:
                    return finish_reason
    return None


def has_tool_calls(response_body: dict[str, Any]) -> bool:
    """Check if response contains tool calls."""
    choices = response_body.get("choices", [])
    if not choices:
        return False
    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls")
    return bool(tool_calls)


def assert_response_expectations(
    response_or_chunks: dict[str, Any] | list[dict[str, Any]],
    assertion: Any,  # ResponseAssertion type
    content: str,
) -> None:
    """Validate response against assertions."""
    # Check text content
    if assertion.should_contain_text:
        for text in assertion.should_contain_text:
            assert text in content, f"Expected '{text}' in response content, got: {content}"

    if assertion.should_not_contain_text:
        for text in assertion.should_not_contain_text:
            assert text not in content, f"Did not expect '{text}' in response content, got: {content}"

    # Check finish_reason
    if assertion.finish_reason:
        actual_finish_reason = extract_finish_reason(response_or_chunks)
        assert actual_finish_reason == assertion.finish_reason, (
            f"Expected finish_reason={assertion.finish_reason}, got {actual_finish_reason}"
        )

    # Check tool calls (non-streaming only)
    if assertion.should_have_tool_calls is not None and isinstance(response_or_chunks, dict):
        has_calls = has_tool_calls(response_or_chunks)
        if assertion.should_have_tool_calls:
            assert has_calls, "Expected response to have tool calls"
        else:
            assert not has_calls, "Expected response to NOT have tool calls"


__all__ = [
    "build_policy_payload",
    "stream_policy_block",
    # Parameterized test helpers
    "build_request_payload",
    "execute_non_streaming_request",
    "execute_streaming_request",
    "extract_message_content",
    "extract_streaming_content",
    "extract_finish_reason",
    "has_tool_calls",
    "assert_response_expectations",
]
