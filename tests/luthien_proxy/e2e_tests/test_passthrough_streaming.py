import httpx
import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer
from tests.luthien_proxy.e2e_tests.mock_gemini.server import MockGeminiServer
from tests.luthien_proxy.e2e_tests.mock_openai.server import MockOpenAIServer

pytestmark = pytest.mark.mock_e2e

_OPENAI_REQUEST = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "hi"}],
}

_GEMINI_REQUEST = {
    "contents": [{"parts": [{"text": "hi"}]}],
}

_ANTHROPIC_REQUEST = {
    "model": "claude-haiku-4-5",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "hi"}],
}


@pytest.mark.asyncio
async def test_openai_streaming_delivers_sse_chunks(
    mock_openai_server: MockOpenAIServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
):
    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{gateway_url}/openai/v1/chat/completions",
            headers=auth_headers,
            json={**_OPENAI_REQUEST, "stream": True},
        ) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_bytes():
                if chunk:
                    chunks.append(chunk)

    assert chunks, "No chunks received"
    all_data = b"".join(chunks)
    assert b"data:" in all_data
    assert b"[DONE]" in all_data


@pytest.mark.asyncio
async def test_gemini_streaming_delivers_sse_chunks(
    mock_gemini_server: MockGeminiServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
):
    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{gateway_url}/gemini/v1beta/models/gemini-1.5-flash:streamGenerateContent",
            headers=auth_headers,
            json=_GEMINI_REQUEST,
        ) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_bytes():
                if chunk:
                    chunks.append(chunk)

    assert chunks, "No chunks received"
    all_data = b"".join(chunks)
    assert b"data:" in all_data


@pytest.mark.asyncio
async def test_anthropic_alias_streaming(
    mock_anthropic: MockAnthropicServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
):
    from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import stream_response

    mock_anthropic.enqueue(stream_response("alias reply"))

    lines: list[str] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async with client.stream(
            "POST",
            f"{gateway_url}/anthropic/v1/messages",
            headers={**auth_headers, "anthropic-version": "2023-06-01"},
            json={**_ANTHROPIC_REQUEST, "stream": True},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                lines.append(line)

    event_types = {line[len("event: ") :].strip() for line in lines if line.startswith("event: ")}
    assert "message_start" in event_types
    assert "message_stop" in event_types


@pytest.mark.asyncio
async def test_openai_non_streaming_returns_buffered_json(
    mock_openai_server: MockOpenAIServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
):
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/openai/v1/chat/completions",
            headers=auth_headers,
            json=_OPENAI_REQUEST,
        )

    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert data["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_gemini_non_streaming_returns_buffered_json(
    mock_gemini_server: MockGeminiServer,
    gateway_healthy,
    gateway_url: str,
    auth_headers: dict,
):
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{gateway_url}/gemini/v1beta/models/gemini-1.5-flash:generateContent",
            headers=auth_headers,
            json=_GEMINI_REQUEST,
        )

    assert response.status_code == 200
    data = response.json()
    assert "candidates" in data
    parts = data["candidates"][0]["content"]["parts"]
    assert any(p.get("text") for p in parts)
