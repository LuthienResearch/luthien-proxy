"""Mock Anthropic API server for e2e testing without real API calls.

Implements the subset of the Anthropic Messages API needed by luthien-proxy:
  POST /v1/messages  →  JSON response or SSE stream

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

from tests.e2e_tests.mock_anthropic.responses import MockResponse, text_response

logger = logging.getLogger(__name__)

DEFAULT_MOCK_PORT = 18888


class MockAnthropicServer:
    """Minimal Anthropic API mock server backed by aiohttp.

    Runs in a dedicated background thread so it remains responsive throughout
    all pytest event loop scopes.

    Responses are consumed from a FIFO queue — enqueue one response per
    expected request. Unexpected extra requests use the default response.
    """

    def __init__(self, port: int = DEFAULT_MOCK_PORT):
        self._port = port
        self._queue: queue.SimpleQueue[MockResponse] = queue.SimpleQueue()
        self._default = text_response("mock response")
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self._port}"

    def enqueue(self, response: MockResponse) -> None:
        """Queue a response to be returned by the next incoming request."""
        self._queue.put(response)

    def set_default(self, response: MockResponse) -> None:
        """Change the default response used when the queue is empty."""
        self._default = response

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

    async def _handle_messages(self, request: web.Request) -> web.StreamResponse | web.Response:
        body = await request.json()

        try:
            mock = self._queue.get_nowait()
        except queue.Empty:
            mock = self._default

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
