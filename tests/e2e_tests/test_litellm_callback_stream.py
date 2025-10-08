import asyncio
import json

import pytest
import websockets

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_callback_emits_end_when_stream_finishes():
    from config.litellm_callback import LuthienCallback
    from litellm.types.utils import ModelResponseStream

    received: list[dict[str, object]] = []

    async def handler(websocket: websockets.WebSocketServerProtocol):
        try:
            async for raw in websocket:
                message = json.loads(raw)
                received.append(message)
                if message.get("type") == "CHUNK":
                    await websocket.send(json.dumps({"type": "CHUNK", "data": message.get("data")}))
        except websockets.ConnectionClosed:  # pragma: no cover - server shutdown
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        callback = LuthienCallback()
        callback.control_plane_url = f"http://127.0.0.1:{port}"

        chunk = ModelResponseStream.model_validate(
            {
                "id": "stream",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "gpt-test",
                "choices": [{"index": 0, "delta": {"content": "hi"}}],
            }
        )

        async def upstream():
            yield chunk

        async for _ in callback.async_post_call_streaming_iterator_hook(
            None,
            upstream(),
            {"litellm_call_id": "stream-end"},
        ):
            pass

        await asyncio.sleep(0.1)
    finally:
        server.close()
        await server.wait_closed()

    message_types = [entry.get("type") for entry in received]
    assert message_types[:2] == ["START", "CHUNK"]
    assert message_types[-1] == "END"
