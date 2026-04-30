import json

import httpx


def test_mock_openai_non_streaming(mock_openai_server):
    base = mock_openai_server.base_url
    mock_openai_server.clear_requests()

    mock_openai_server.enqueue(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )

    response = httpx.post(
        f"{base}/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-key"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello"
    assert data["choices"][0]["finish_reason"] == "stop"

    headers = mock_openai_server.last_request_headers()
    assert headers is not None
    assert "sk-test-key" in headers.get("Authorization", "")


def test_mock_openai_streaming(mock_openai_server):
    base = mock_openai_server.base_url
    mock_openai_server.clear_requests()

    mock_openai_server.enqueue(
        [
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1234567890,
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}],
            },
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1234567890,
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
    )

    with httpx.stream(
        "POST",
        f"{base}/v1/chat/completions",
        headers={"Authorization": "Bearer sk-stream-key"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        timeout=10,
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        lines = list(resp.iter_lines())

    data_lines = [line for line in lines if line.startswith("data:")]
    assert any("[DONE]" in line for line in data_lines)

    content_chunks = []
    for line in data_lines:
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            continue
        chunk = json.loads(payload)
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if "content" in delta:
                content_chunks.append(delta["content"])

    assert "".join(content_chunks) == "Hello"

    headers = mock_openai_server.last_request_headers()
    assert headers is not None
    assert "sk-stream-key" in headers.get("Authorization", "")


def test_mock_openai_default_response(mock_openai_server):
    base = mock_openai_server.base_url
    mock_openai_server.drain_queue()
    mock_openai_server.clear_requests()

    response = httpx.post(
        f"{base}/v1/chat/completions",
        headers={"Authorization": "Bearer any-key"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "mock response"


def test_mock_openai_captures_headers(mock_openai_server):
    base = mock_openai_server.base_url
    mock_openai_server.clear_requests()

    httpx.post(
        f"{base}/v1/chat/completions",
        headers={"Authorization": "Bearer my-secret-key", "X-Custom": "custom-value"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )

    all_headers = mock_openai_server.received_request_headers()
    assert len(all_headers) >= 1
    last = mock_openai_server.last_request_headers()
    assert "my-secret-key" in last.get("Authorization", "")
    assert last.get("X-Custom") == "custom-value"
