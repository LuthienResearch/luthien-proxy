"""In-process mock Anthropic LLM client for e2e testing."""

import json
import queue
import threading
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

import httpx
from anthropic import APIStatusError
from anthropic.types import (
    InputJSONDelta,
    Message,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)
from anthropic.types.raw_message_delta_event import Delta
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import (
    AnyMockResponse,
    MockErrorResponse,
    MockParallelToolResponse,
    MockToolResponse,
    text_response,
)

from luthien_proxy.llm.types.anthropic import AnthropicContentBlock, AnthropicRequest, AnthropicResponse, build_usage

if TYPE_CHECKING:
    from anthropic.lib.streaming import MessageStreamEvent


def _make_status_error(mock: MockErrorResponse) -> APIStatusError:
    body = {"type": "error", "error": {"type": mock.error_type, "message": mock.error_message}}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(mock.status_code, json=body, request=request)
    return APIStatusError(mock.error_message, response=response, body=body)


def _tool_block_events(index: int, tool_id: str, tool_name: str, tool_input: dict) -> list[Any]:
    events: list[Any] = [
        RawContentBlockStartEvent(
            type="content_block_start",
            index=index,
            content_block=ToolUseBlock(type="tool_use", id=tool_id, name=tool_name, input={}),
        )
    ]
    input_json = json.dumps(tool_input)
    chunk_size = 10
    for i in range(0, len(input_json), chunk_size):
        events.append(
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=index,
                delta=InputJSONDelta(type="input_json_delta", partial_json=input_json[i : i + chunk_size]),
            )
        )
    events.append(RawContentBlockStopEvent(type="content_block_stop", index=index))
    return events


class MockAnthropicClient:
    _base_url: str | None = None

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._default: AnyMockResponse = text_response("mock response")
        self._received_requests: list[dict] = []
        self._received_headers: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def enqueue(self, response: AnyMockResponse) -> None:
        self._queue.put(response)

    def set_default(self, response: AnyMockResponse) -> None:
        self._default = response

    def drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def last_request(self) -> dict | None:
        with self._lock:
            return self._received_requests[-1] if self._received_requests else None

    def received_requests(self) -> list[dict]:
        with self._lock:
            return list(self._received_requests)

    def last_request_headers(self) -> dict[str, str] | None:
        with self._lock:
            return self._received_headers[-1] if self._received_headers else None

    def received_request_headers(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._received_headers)

    def clear_requests(self) -> None:
        with self._lock:
            self._received_requests.clear()
            self._received_headers.clear()

    def _dequeue(self) -> AnyMockResponse:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return self._default

    def _record(self, request: AnthropicRequest, extra_headers: dict[str, str] | None) -> None:
        with self._lock:
            self._received_requests.append(dict(request))
            self._received_headers.append(extra_headers or {})

    def _tool_complete_response(
        self, mock: MockToolResponse | MockParallelToolResponse, request: AnthropicRequest
    ) -> AnthropicResponse:
        model = cast(str, request.get("model", mock.model))
        if isinstance(mock, MockParallelToolResponse):
            content = cast(
                list[AnthropicContentBlock],
                [
                    {"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:24]}", "name": name, "input": tool_input}
                    for name, tool_input in mock.tools
                ],
            )
        else:
            content = cast(
                list[AnthropicContentBlock],
                [
                    {
                        "type": "tool_use",
                        "id": mock.tool_id or f"toolu_{uuid.uuid4().hex[:24]}",
                        "name": mock.tool_name,
                        "input": mock.tool_input,
                    }
                ],
            )
        return AnthropicResponse(
            id=f"msg_{uuid.uuid4().hex[:24]}",
            type="message",
            role="assistant",
            content=content,
            model=model,
            stop_reason="tool_use",
            stop_sequence=None,
            usage=build_usage(mock.input_tokens, mock.output_tokens),
        )

    async def complete(
        self, request: AnthropicRequest, extra_headers: dict[str, str] | None = None
    ) -> AnthropicResponse:
        mock = self._dequeue()
        self._record(request, extra_headers)
        if isinstance(mock, MockErrorResponse):
            raise _make_status_error(mock)
        if isinstance(mock, (MockToolResponse, MockParallelToolResponse)):
            return self._tool_complete_response(mock, request)
        return AnthropicResponse(
            id=f"msg_{uuid.uuid4().hex[:24]}",
            type="message",
            role="assistant",
            content=cast(list[AnthropicContentBlock], [{"type": "text", "text": mock.text}]),
            model=cast(str, request.get("model", mock.model)),
            stop_reason=mock.stop_reason,  # type: ignore[typeddict-item]
            stop_sequence=None,
            usage=build_usage(mock.input_tokens, mock.output_tokens),
        )

    async def stream(  # type: ignore[override]
        self, request: AnthropicRequest, extra_headers: dict[str, str] | None = None
    ) -> AsyncIterator["MessageStreamEvent"]:
        mock = self._dequeue()
        self._record(request, extra_headers)
        if isinstance(mock, MockErrorResponse):
            raise _make_status_error(mock)

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        model = cast(str, request.get("model", mock.model))

        yield RawMessageStartEvent(  # type: ignore[misc]
            type="message_start",
            message=Message(
                id=msg_id,
                type="message",
                role="assistant",
                content=[],
                model=model,
                stop_reason=None,
                stop_sequence=None,
                usage=Usage(input_tokens=mock.input_tokens, output_tokens=1),
            ),
        )

        if isinstance(mock, MockParallelToolResponse):
            for i, (name, tool_input) in enumerate(mock.tools):
                for event in _tool_block_events(i, f"toolu_{uuid.uuid4().hex[:24]}", name, tool_input):
                    yield event  # type: ignore[misc]
        elif isinstance(mock, MockToolResponse):
            tool_id = mock.tool_id or f"toolu_{uuid.uuid4().hex[:24]}"
            for event in _tool_block_events(0, tool_id, mock.tool_name, mock.tool_input):
                yield event  # type: ignore[misc]
        else:
            yield RawContentBlockStartEvent(  # type: ignore[misc]
                type="content_block_start",
                index=0,
                content_block=TextBlock(type="text", text=""),
            )
            for chunk in mock.get_chunks():
                yield RawContentBlockDeltaEvent(  # type: ignore[misc]
                    type="content_block_delta",
                    index=0,
                    delta=TextDelta(type="text_delta", text=chunk),
                )
            yield RawContentBlockStopEvent(type="content_block_stop", index=0)  # type: ignore[misc]

        stop_reason = "tool_use" if isinstance(mock, (MockToolResponse, MockParallelToolResponse)) else mock.stop_reason
        yield RawMessageDeltaEvent(  # type: ignore[misc]
            type="message_delta",
            delta=Delta(stop_reason=stop_reason, stop_sequence=None),  # type: ignore[arg-type]
            usage=MessageDeltaUsage(output_tokens=mock.output_tokens),
        )
        yield RawMessageStopEvent(type="message_stop")  # type: ignore[misc]


__all__ = ["MockAnthropicClient"]
