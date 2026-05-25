import json

import httpx


def test_mock_gemini_non_streaming(mock_gemini_server):
    base = mock_gemini_server.base_url
    mock_gemini_server.clear_requests()

    mock_gemini_server.enqueue(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Hello"}], "role": "model"},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        }
    )

    response = httpx.post(
        f"{base}/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"x-goog-api-key": "test-gemini-key"},
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        timeout=10,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "Hello"
    assert data["candidates"][0]["finishReason"] == "STOP"

    headers = mock_gemini_server.last_request_headers()
    assert headers is not None
    assert "test-gemini-key" in headers.get("x-goog-api-key", "")


def test_mock_gemini_streaming(mock_gemini_server):
    base = mock_gemini_server.base_url
    mock_gemini_server.clear_requests()

    mock_gemini_server.enqueue(
        [
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Hello"}], "role": "model"},
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ]
            }
        ]
    )

    with httpx.stream(
        "POST",
        f"{base}/v1beta/models/gemini-1.5-flash:streamGenerateContent",
        headers={"x-goog-api-key": "test-stream-key"},
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        timeout=10,
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        lines = list(resp.iter_lines())

    data_lines = [line for line in lines if line.startswith("data:")]
    assert len(data_lines) >= 1

    parsed = json.loads(data_lines[0][len("data:") :].strip())
    assert parsed["candidates"][0]["content"]["parts"][0]["text"] == "Hello"

    headers = mock_gemini_server.last_request_headers()
    assert headers is not None
    assert "test-stream-key" in headers.get("x-goog-api-key", "")


def test_mock_gemini_default_response(mock_gemini_server):
    base = mock_gemini_server.base_url
    mock_gemini_server.drain_queue()
    mock_gemini_server.clear_requests()

    response = httpx.post(
        f"{base}/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"x-goog-api-key": "any-key"},
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        timeout=10,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["candidates"][0]["content"]["parts"][0]["text"] == "mock response"


def test_mock_gemini_captures_headers(mock_gemini_server):
    base = mock_gemini_server.base_url
    mock_gemini_server.clear_requests()

    httpx.post(
        f"{base}/v1beta/models/gemini-1.5-flash:generateContent",
        headers={"x-goog-api-key": "my-gemini-key", "X-Custom": "custom-value"},
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        timeout=10,
    )

    all_headers = mock_gemini_server.received_request_headers()
    assert len(all_headers) >= 1
    last = mock_gemini_server.last_request_headers()
    assert "my-gemini-key" in last.get("x-goog-api-key", "")
    assert last.get("X-Custom") == "custom-value"
