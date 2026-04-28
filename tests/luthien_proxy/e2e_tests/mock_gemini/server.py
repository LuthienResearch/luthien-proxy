"""Mock Gemini API server for e2e testing.

Implements the subset of the Gemini API needed by luthien-proxy passthrough tests:
  POST /v1beta/models/{model}:generateContent       →  JSON response
  POST /v1beta/models/{model}:streamGenerateContent →  SSE stream

Follows the same pattern as mock_anthropic/server.py: dedicated background thread
with its own event loop, FIFO response queue, thread-safe request/header capture.

Usage:
    server = MockGeminiServer()
    server.start()

    server.enqueue({"candidates": [{"content": {"parts": [{"text": "Hi"}], "role": "model"}}]})

    server.stop()

If no response is enqueued, a default canned response is returned.
"""

import asyncio
import json
import logging
import queue
import threading

from aiohttp import web

logger = logging.getLogger(__name__)

DEFAULT_MOCK_PORT = 18890

_DEFAULT_RESPONSE = {
    "candidates": [
        {
            "content": {"parts": [{"text": "mock response"}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
}


class MockGeminiServer:
    """Minimal Gemini API mock backed by aiohttp.

    Runs in a dedicated background thread with its own event loop so it remains
    responsive regardless of what the pytest event loop is doing.

    Responses are consumed from a FIFO queue. Enqueue a dict response before
    each test request; extra requests fall back to the default canned response.

    For streaming (:streamGenerateContent), the response dict is emitted as a
    single SSE data chunk. To emit multiple chunks, enqueue a list of dicts.
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
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mock-gemini")
        self._thread.start()
        self._ready.wait(timeout=10)
        if not self._ready.is_set():
            raise RuntimeError("MockGeminiServer failed to start within 10 seconds")
        logger.info(f"MockGeminiServer ready on port {self._port}")

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
        app.router.add_post(r"/v1beta/models/{model}:generateContent", self._handle_generate)
        app.router.add_post(r"/v1beta/models/{model}:streamGenerateContent", self._handle_stream_generate)
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

    async def _handle_generate(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._record_request(body, dict(request.headers))
        enqueued = self._next_response()
        data = enqueued[0] if isinstance(enqueued, list) else enqueued
        return web.Response(body=json.dumps(data), content_type="application/json")

    async def _handle_stream_generate(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self._record_request(body, dict(request.headers))
        enqueued = self._next_response()
        chunks = enqueued if isinstance(enqueued, list) else [enqueued]

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

        await response.write_eof()
        return response
