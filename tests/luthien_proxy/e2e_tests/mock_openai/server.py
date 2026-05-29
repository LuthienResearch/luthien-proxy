"""Mock OpenAI Chat Completions server for e2e testing.

Implements the subset of the OpenAI API needed by luthien-proxy passthrough tests:
  POST /v1/chat/completions  →  JSON response or SSE stream (OpenAI format)

Follows the same pattern as mock_anthropic/server.py: dedicated background thread
with its own event loop, FIFO response queue, thread-safe request/header capture.

Usage:
    server = MockOpenAIServer()
    server.start()

    server.enqueue({"choices": [{"message": {"role": "assistant", "content": "Hi"}}]})

    server.stop()

If no response is enqueued, a default canned response is returned.
"""

import asyncio
import json
import logging
import queue
import threading
import time

from aiohttp import web

logger = logging.getLogger(__name__)

DEFAULT_MOCK_PORT = 18889

_DEFAULT_RESPONSE = {
    "id": "chatcmpl-default",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "mock response"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

_DEFAULT_STREAMING_CHUNKS = [
    {
        "id": "chatcmpl-default",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4o",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "mock"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-default",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4o",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
]


class MockOpenAIServer:
    """Minimal OpenAI Chat Completions mock backed by aiohttp.

    Runs in a dedicated background thread with its own event loop so it remains
    responsive regardless of what the pytest event loop is doing.

    Responses are consumed from a FIFO queue. Each enqueued item may be:
      - dict: for non-streaming, returned as JSON; for streaming, treated as
        a single-chunk SSE stream ending with [DONE]
      - list[dict]: for streaming, each dict is emitted as one SSE data line
        followed by [DONE]

    The ``stream`` field in the request body determines the response format.
    If the enqueued item has ``"_streaming_chunks"`` key (list), those chunks
    are always used for streaming regardless of request body.
    """

    def __init__(self, port: int = DEFAULT_MOCK_PORT):
        self._port = port
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._received_requests: list[dict] = []
        self._received_headers: list[dict[str, str]] = []
        self._requests_lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self._port}"

    def enqueue(self, response: dict | list) -> None:
        self._queue.put(response)

    def last_request(self) -> dict | None:
        with self._requests_lock:
            return self._received_requests[-1] if self._received_requests else None

    def received_requests(self) -> list[dict]:
        with self._requests_lock:
            return list(self._received_requests)

    def last_request_headers(self) -> dict[str, str] | None:
        with self._requests_lock:
            return self._received_headers[-1] if self._received_headers else None

    def received_request_headers(self) -> list[dict[str, str]]:
        with self._requests_lock:
            return list(self._received_headers)

    def clear_requests(self) -> None:
        with self._requests_lock:
            self._received_requests.clear()
            self._received_headers.clear()

    def drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mock-openai")
        self._thread.start()
        self._ready.wait(timeout=10)
        if not self._ready.is_set():
            raise RuntimeError("MockOpenAIServer failed to start within 10 seconds")
        logger.info(f"MockOpenAIServer ready on port {self._port}")

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        self._ready.set()
        await self._stop_event.wait()
        await self._runner.cleanup()

    def _record_request(self, body: dict, headers: dict[str, str] | None = None) -> None:
        with self._requests_lock:
            self._received_requests.append(body)
            self._received_headers.append(headers or {})

    def _next_response(self) -> dict | list:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return _DEFAULT_RESPONSE

    async def _handle_chat_completions(self, request: web.Request) -> web.StreamResponse | web.Response:
        body = await request.json()
        self._record_request(body, dict(request.headers))

        enqueued = self._next_response()
        want_stream = body.get("stream", False)

        if want_stream:
            return await self._stream_response(enqueued, request)
        return self._json_response(enqueued)

    def _json_response(self, enqueued: dict | list) -> web.Response:
        if isinstance(enqueued, list):
            content = ""
            for chunk in enqueued:
                for choice in chunk.get("choices", []):
                    content += choice.get("delta", {}).get("content", "")
            data = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "gpt-4o",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        else:
            data = enqueued
        return web.Response(body=json.dumps(data), content_type="application/json")

    async def _stream_response(self, enqueued: dict | list, request: web.Request) -> web.StreamResponse:
        if isinstance(enqueued, list):
            chunks = enqueued
        else:
            content = ""
            for choice in enqueued.get("choices", []):
                msg = choice.get("message", {})
                content += msg.get("content", "")
            created = enqueued.get("created", int(time.time()))
            model = enqueued.get("model", "gpt-4o")
            cid = enqueued.get("id", "chatcmpl-test")
            chunks = [
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}
                    ],
                },
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]

        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        for chunk in chunks:
            line = f"data: {json.dumps(chunk)}\n\n"
            await response.write(line.encode())

        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response
