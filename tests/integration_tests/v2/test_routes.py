import json
from typing import Any, AsyncIterator

import litellm
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.unit_tests.v2.conftest import create_test_lifespan

from luthien_proxy.v2.routes import router


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish_event(self, call_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append(
            {
                "call_id": call_id,
                "event_type": event_type,
                "data": data,
            }
        )


class FakeUsage:
    def __init__(self, prompt_tokens: int = 1, completion_tokens: int = 1) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeChoiceMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoiceDelta:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str, finish_reason: str | None) -> None:
        self.message = FakeChoiceMessage(content)
        self.delta = FakeChoiceDelta(content)
        self.finish_reason = finish_reason


class FakeModelResponse:
    def __init__(
        self,
        *,
        id: str,
        model: str,
        content: str,
        finish_reason: str | None = "stop",
        usage: FakeUsage | None = None,
    ) -> None:
        self.id = id
        self.model = model
        self.choices = [FakeChoice(content, finish_reason)]
        self.usage = usage or FakeUsage()

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "choices": [
                {
                    "message": {"content": self.choices[0].message.content},
                    "delta": {"content": self.choices[0].delta.content},
                    "finish_reason": self.choices[0].finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
            },
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump())


class FakeControlPlane:
    def __init__(self) -> None:
        self.request_calls: list[tuple[Any, str]] = []
        self.full_response_calls: list[tuple[Any, str]] = []
        self.stream_calls: list[str] = []

    async def process_request(self, request: Any, call_id: str):
        self.request_calls.append((request, call_id))
        return request

    async def process_full_response(self, response: FakeModelResponse, call_id: str) -> FakeModelResponse:
        self.full_response_calls.append((response, call_id))
        adapted_content = f"policy:{response.choices[0].message.content}"
        return FakeModelResponse(
            id="final-response",
            model=response.model,
            content=adapted_content,
            finish_reason=response.choices[0].finish_reason,
            usage=response.usage,
        )

    def process_streaming_response(
        self,
        incoming: AsyncIterator[FakeModelResponse],
        call_id: str,
        **_: Any,
    ) -> AsyncIterator[FakeModelResponse]:
        self.stream_calls.append(call_id)

        async def _generator() -> AsyncIterator[FakeModelResponse]:
            async for chunk in incoming:
                yield chunk

        return _generator()


class FakeStream:
    def __init__(self, chunks: list[FakeModelResponse]) -> None:
        self._chunks = chunks
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


def build_test_app() -> tuple[FastAPI, FakeControlPlane, FakeEventPublisher]:
    """Build test app using the same lifespan pattern as production.

    This ensures tests use the same initialization flow as production code,
    but with mocked dependencies instead of real DB/Redis connections.
    """
    control_plane = FakeControlPlane()
    event_publisher = FakeEventPublisher()

    # Use the test lifespan factory (same pattern as production)
    lifespan = create_test_lifespan(
        control_plane=control_plane,
        event_publisher=event_publisher,
        db_pool=None,  # No DB needed for these tests
        redis_client=None,  # No Redis needed for these tests
        api_key="test-api-key",
    )

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app, control_plane, event_publisher


@pytest.mark.asyncio
async def test_openai_chat_completions_returns_policy_response(monkeypatch: pytest.MonkeyPatch):
    app, control_plane, event_publisher = build_test_app()

    async def fake_completion(**kwargs):
        fake_completion.calls.append(kwargs)
        return FakeModelResponse(
            id="raw-response",
            model=kwargs.get("model", "gpt-4o"),
            content="llm output",
        )

    fake_completion.calls: list[dict[str, Any]] = []
    monkeypatch.setattr(litellm, "acompletion", fake_completion)

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False,
        "verbosity": "high",
        "metadata": {"trace_id": "trace-123"},
    }

    # Wrap test in lifespan context to initialize app.state
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/v2/chat/completions", json=payload)
        await transport.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "policy:llm output"

    assert fake_completion.calls[0]["allowed_openai_params"] == ["verbosity"]
    assert control_plane.request_calls
    original_request = control_plane.request_calls[0][0]
    assert original_request.model == "gpt-4o"

    assert control_plane.full_response_calls
    assert control_plane.full_response_calls[0][0].choices[0].message.content == "llm output"

    recorded_events = [event["event_type"] for event in event_publisher.events]
    assert recorded_events == [
        "gateway.request_received",
        "gateway.request_sent",
        "gateway.response_received",
        "gateway.response_sent",
    ]


@pytest.mark.asyncio
async def test_openai_chat_completions_streaming_emits_sse(monkeypatch: pytest.MonkeyPatch):
    app, control_plane, _ = build_test_app()

    chunks = [
        FakeModelResponse(id="chunk-1", model="gpt-4o", content="first chunk", finish_reason=None),
        FakeModelResponse(id="chunk-2", model="gpt-4o", content="second chunk", finish_reason=None),
    ]

    async def fake_completion(**kwargs):
        fake_completion.calls.append(kwargs)
        return FakeStream(chunks)

    fake_completion.calls: list[dict[str, Any]] = []
    monkeypatch.setattr(litellm, "acompletion", fake_completion)

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Stream please"}],
        "stream": True,
        "verbosity": "verbose",
    }

    # Wrap test in lifespan context to initialize app.state
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream("POST", "/v2/chat/completions", json=payload) as response:
                assert response.status_code == 200
                data_lines = []
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_lines.append(line)
        await transport.aclose()

    assert len(data_lines) == 2
    first_payload = json.loads(data_lines[0].split("data: ", 1)[1])
    assert first_payload["choices"][0]["delta"]["content"] == "first chunk"

    assert len(control_plane.stream_calls) == 1
    assert fake_completion.calls[0]["stream"] is True
    assert fake_completion.calls[0]["allowed_openai_params"] == ["verbosity"]


@pytest.mark.asyncio
async def test_anthropic_messages_returns_converted_response(monkeypatch: pytest.MonkeyPatch):
    app, control_plane, event_publisher = build_test_app()

    async def fake_completion(**kwargs):
        fake_completion.calls.append(kwargs)
        return FakeModelResponse(
            id="anthropic-raw",
            model=kwargs.get("model", "claude-3-sonnet"),
            content="llm reply",
        )

    fake_completion.calls: list[dict[str, Any]] = []
    monkeypatch.setattr(litellm, "acompletion", fake_completion)

    payload = {
        "model": "claude-3-sonnet",
        "system": "You are helpful.",
        "messages": [{"role": "user", "content": "Tell me a joke."}],
        "verbosity": "high",
    }

    # Wrap test in lifespan context to initialize app.state
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/v2/messages", json=payload)
        await transport.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["content"][0]["text"] == "policy:llm reply"
    assert body["stop_reason"] == "end_turn"

    litellm_call = fake_completion.calls[0]
    assert litellm_call["messages"][0]["role"] == "system"

    assert control_plane.request_calls
    assert control_plane.request_calls[0][0].model == "claude-3-sonnet"
    assert event_publisher.events[0]["event_type"] == "gateway.request_received"


@pytest.mark.asyncio
async def test_anthropic_messages_streaming_converts_chunks(monkeypatch: pytest.MonkeyPatch):
    app, control_plane, _ = build_test_app()

    chunks = [
        FakeModelResponse(id="chunk-1", model="claude-3-sonnet", content="delta one", finish_reason=None),
    ]

    async def fake_completion(**kwargs):
        fake_completion.calls.append(kwargs)
        return FakeStream(chunks)

    fake_completion.calls: list[dict[str, Any]] = []
    monkeypatch.setattr(litellm, "acompletion", fake_completion)

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "stream anthro"}],
        "stream": True,
    }

    # Wrap test in lifespan context to initialize app.state
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream("POST", "/v2/messages", json=payload) as response:
                assert response.status_code == 200
                sse_payloads = []
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        sse_payloads.append(line)
        await transport.aclose()

    assert len(sse_payloads) == 1
    chunk_payload = json.loads(sse_payloads[0].split("data: ", 1)[1])
    assert chunk_payload["type"] == "content_block_delta"
    assert chunk_payload["delta"]["text"] == "delta one"

    assert len(control_plane.stream_calls) == 1
    assert fake_completion.calls[0]["stream"] is True
