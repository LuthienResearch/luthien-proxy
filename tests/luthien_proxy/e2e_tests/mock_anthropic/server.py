"""Mock Anthropic API server for e2e testing without real API calls.

Implements the subset of the Anthropic Messages API needed by luthien-proxy:
  POST /v1/messages         →  JSON response or SSE stream (Anthropic format)

The server runs in a dedicated background thread with its own event loop so it
remains responsive regardless of what the pytest event loop is doing. This avoids
a pytest-asyncio issue where session-scoped async fixtures share a loop with the
test runner — when a test's function-scoped loop runs, the session loop is paused,
making any server in that loop unable to accept connections.

Usage:
    server = MockAnthropicServer()
    server.start()   # synchronous — starts background thread

    server.enqueue(text_response("Hello world"))   # next request gets this
    # ... run test ...

    server.stop()    # synchronous — stops background thread

If no response is enqueued, the default response ("mock response") is returned.
"""

import asyncio
import json
import logging
import queue
import threading
import uuid

from aiohttp import web
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import (
    MockErrorResponse,
    MockParallelToolResponse,
    MockResponse,
    MockToolResponse,
    text_response,
)

logger = logging.getLogger(__name__)

DEFAULT_MOCK_PORT = 18888

# Union of all response types the queue can hold.
AnyMockResponse = MockResponse | MockErrorResponse | MockToolResponse | MockParallelToolResponse


class MockAnthropicServer:
    """Minimal Anthropic API mock server backed by aiohttp.

    Runs in a dedicated background thread so it remains responsive throughout
    all pytest event loop scopes.

    Responses are consumed from a FIFO queue — enqueue one response per
    expected request. Unexpected extra requests use the default response.

    Request capture:
        All incoming request bodies are stored and accessible via
        ``last_request()``, ``received_requests()``, and ``clear_requests()``.
        Access is protected by a threading.Lock because the server runs in a
        background thread while tests access from the main thread.
    """

    def __init__(self, port: int = DEFAULT_MOCK_PORT):
        self._port = port
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._default: AnyMockResponse = text_response("mock response")
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._received_requests: list[dict] = []
        self._received_headers: list[dict[str, str]] = []
        self._requests_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self._port}"

    def enqueue(self, response: AnyMockResponse) -> None:
        """Queue a response to be returned by the next incoming request."""
        self._queue.put(response)

    def set_default(self, response: AnyMockResponse) -> None:
        """Change the default response used when the queue is empty."""
        self._default = response

    def last_request(self) -> dict | None:
        """Return the most recently received request body, or None if no requests yet."""
        with self._requests_lock:
            return self._received_requests[-1] if self._received_requests else None

    def received_requests(self) -> list[dict]:
        """Return a copy of all received request bodies in order."""
        with self._requests_lock:
            return list(self._received_requests)

    def last_request_headers(self) -> dict[str, str] | None:
        """Return headers of the most recently received request, or None."""
        with self._requests_lock:
            return self._received_headers[-1] if self._received_headers else None

    def received_request_headers(self) -> list[dict[str, str]]:
        """Return headers of all received requests in order."""
        with self._requests_lock:
            return list(self._received_headers)

    def clear_requests(self) -> None:
        """Clear the recorded request history."""
        with self._requests_lock:
            self._received_requests.clear()
            self._received_headers.clear()

    def drain_queue(self) -> None:
        """Drain all pending items from the response queue (for test isolation)."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def start(self) -> None:
        """Start the mock server in a background thread. Blocks until ready."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mock-anthropic")
        self._thread.start()
        self._ready.wait(timeout=10)
        if not self._ready.is_set():
            raise RuntimeError("MockAnthropicServer failed to start within 10 seconds")
        logger.info(f"MockAnthropicServer ready on port {self._port}")

    def stop(self) -> None:
        """Stop the mock server and background thread."""
        if self._loop and self._stop_event:
            # Signal _serve() to exit cleanly
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Entry point for the background thread — owns its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        app = web.Application()
        app.router.add_post("/v1/messages", self._handle_messages)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        self._ready.set()
        # Wait until stop() signals us to exit
        await self._stop_event.wait()
        await self._runner.cleanup()

    def _record_request(self, body: dict, headers: dict[str, str] | None = None) -> None:
        """Thread-safely append a parsed request body and headers to the history."""
        with self._requests_lock:
            self._received_requests.append(body)
            self._received_headers.append(headers or {})

    def _next_mock(self) -> AnyMockResponse:
        """Dequeue the next mock response, falling back to the default."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return self._default

    # ------------------------------------------------------------------
    # Anthropic /v1/messages
    # ------------------------------------------------------------------

    async def _handle_messages(self, request: web.Request) -> web.StreamResponse | web.Response:
        body = await request.json()
        self._record_request(body, dict(request.headers))

        mock = self._next_mock()

        if isinstance(mock, MockErrorResponse):
            return web.Response(
                status=mock.status_code,
                body=json.dumps({"type": "error", "error": {"type": mock.error_type, "message": mock.error_message}}),
                content_type="application/json",
            )

        if isinstance(mock, MockParallelToolResponse):
            if body.get("stream", False):
                return await self._stream_parallel_tool_response(mock, request, body)
            return self._json_parallel_tool_response(mock, body)

        if isinstance(mock, MockToolResponse):
            if body.get("stream", False):
                return await self._stream_tool_response(mock, request, body)
            return self._json_tool_response(mock, body)

        # MockResponse (text)
        if body.get("stream", False):
            return await self._stream_response(mock, request, body)
        return self._json_response(mock, body)

    def _json_response(self, mock: MockResponse, body: dict) -> web.Response:
        data = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": mock.text}],
            "model": body.get("model", mock.model),
            "stop_reason": mock.stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": mock.input_tokens,
                "output_tokens": mock.output_tokens,
            },
        }
        return web.Response(body=json.dumps(data), content_type="application/json")

    def _json_tool_response(self, mock: MockToolResponse, body: dict) -> web.Response:
        tool_id = mock.tool_id or f"toolu_{uuid.uuid4().hex[:24]}"
        data = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": mock.tool_name,
                    "input": mock.tool_input,
                }
            ],
            "model": body.get("model", mock.model),
            "stop_reason": mock.stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": mock.input_tokens,
                "output_tokens": mock.output_tokens,
            },
        }
        return web.Response(body=json.dumps(data), content_type="application/json")

    async def _stream_response(
        self,
        mock: MockResponse,
        request: web.Request,
        body: dict,
    ) -> web.StreamResponse:
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        model = body.get("model", mock.model)
        chunks = mock.get_chunks()

        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        async def emit(event_type: str, data: dict) -> None:
            line = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            await response.write(line.encode())

        await emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": mock.input_tokens, "output_tokens": 1},
                },
            },
        )
        await emit(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        )
        await emit("ping", {"type": "ping"})

        for chunk in chunks:
            await emit(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}},
            )

        await emit("content_block_stop", {"type": "content_block_stop", "index": 0})
        await emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": mock.stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": mock.output_tokens},
            },
        )
        await emit("message_stop", {"type": "message_stop"})

        await response.write_eof()
        return response

    async def _stream_tool_response(
        self,
        mock: MockToolResponse,
        request: web.Request,
        body: dict,
    ) -> web.StreamResponse:
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        tool_id = mock.tool_id or f"toolu_{uuid.uuid4().hex[:24]}"
        model = body.get("model", mock.model)

        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        async def emit(event_type: str, data: dict) -> None:
            line = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            await response.write(line.encode())

        await emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": mock.input_tokens, "output_tokens": 1},
                },
            },
        )
        await emit(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": mock.tool_name,
                    "input": {},
                },
            },
        )

        # Split the serialized tool input into ~10-character chunks
        input_json = json.dumps(mock.tool_input)
        chunk_size = 10
        input_chunks = [input_json[i : i + chunk_size] for i in range(0, len(input_json), chunk_size)]
        for partial in input_chunks:
            await emit(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": partial},
                },
            )

        await emit("content_block_stop", {"type": "content_block_stop", "index": 0})
        await emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": mock.output_tokens},
            },
        )
        await emit("message_stop", {"type": "message_stop"})

        await response.write_eof()
        return response

    def _json_parallel_tool_response(self, mock: MockParallelToolResponse, body: dict) -> web.Response:
        content = []
        for i, (name, tool_input) in enumerate(mock.tools):
            content.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": name,
                    "input": tool_input,
                }
            )
        data = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": content,
            "model": body.get("model", mock.model),
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {
                "input_tokens": mock.input_tokens,
                "output_tokens": mock.output_tokens,
            },
        }
        return web.Response(body=json.dumps(data), content_type="application/json")

    async def _stream_parallel_tool_response(
        self,
        mock: MockParallelToolResponse,
        request: web.Request,
        body: dict,
    ) -> web.StreamResponse:
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        model = body.get("model", mock.model)

        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        async def emit(event_type: str, data: dict) -> None:
            line = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            await response.write(line.encode())

        await emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": mock.input_tokens, "output_tokens": 1},
                },
            },
        )

        for i, (name, tool_input) in enumerate(mock.tools):
            tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
            await emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": i,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": {},
                    },
                },
            )

            input_json = json.dumps(tool_input)
            chunk_size = 10
            for j in range(0, len(input_json), chunk_size):
                await emit(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": input_json[j : j + chunk_size]},
                    },
                )

            await emit("content_block_stop", {"type": "content_block_stop", "index": i})

        await emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": mock.output_tokens},
            },
        )
        await emit("message_stop", {"type": "message_stop"})

        await response.write_eof()
        return response
