"""End-to-end coverage for non-streaming policy response overrides."""

import asyncio
import contextlib
import json

import pytest

pytestmark = pytest.mark.e2e


class _HookServer:
    """Minimal HTTP server that returns a policy override payload."""

    def __init__(self, override_payload: dict[str, object]) -> None:
        self._override_payload = override_payload
        self.requests: list[dict[str, object]] = []
        self._server: asyncio.AbstractServer | None = None
        self.port: int | None = None

    async def __aenter__(self) -> "_HookServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = self._server.sockets or []
        if not sockets:  # pragma: no cover - defensive
            raise RuntimeError("server failed to bind to a socket")
        self.port = sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return

            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                name, value = line.decode().split(":", 1)
                headers[name.strip().lower()] = value.strip()

            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            try:
                payload = json.loads(body.decode() or "{}")
            except json.JSONDecodeError:  # pragma: no cover - defensive
                payload = {}
            self.requests.append(payload)

            response_bytes = json.dumps(self._override_payload).encode()
            writer.write(b"HTTP/1.1 200 OK\r\n")
            writer.write(b"content-type: application/json\r\n")
            writer.write(f"content-length: {len(response_bytes)}\r\n\r\n".encode())
            writer.write(response_bytes)
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


@pytest.mark.asyncio
async def test_policy_override_mutates_response():
    """Ensure async_post_call_success_hook applies control-plane overrides in-place."""
    from config.litellm_callback import LuthienCallback
    from litellm.types.utils import ModelResponse

    override = {
        "id": "chatcmpl-overridden",
        "created": 42,
        "model": "gpt-overridden",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "BLOCKED by policy",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
    }

    async with _HookServer(override) as server:
        callback = LuthienCallback()
        callback.control_plane_url = f"http://127.0.0.1:{server.port}"

        response = ModelResponse(
            model="gpt-initial",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "allowed"},
                }
            ],
        )
        original_identity = id(response)

        hook_result = await callback.async_post_call_success_hook(
            {"stream": False, "litellm_call_id": "call-123"},
            None,
            response,
        )

    assert hook_result == override
    assert id(response) == original_identity
    assert response.model == "gpt-overridden"
    assert response.choices[0]["message"]["content"] == "BLOCKED by policy"
    assert response.usage.total_tokens == 12

    assert len(server.requests) == 1
    assert server.requests[0]["data"]["litellm_call_id"] == "call-123"
