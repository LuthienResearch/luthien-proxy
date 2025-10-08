from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
from typing import Iterator

import httpx
import pytest

pytestmark = pytest.mark.e2e

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.dummy.yaml"
PROXY_URL = "http://localhost:4400"
CONTROL_PLANE_URL = "http://localhost:8083/health"
MASTER_KEY = "sk-luthien-dev-key"


def _run_compose(*args: str) -> None:
    command = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    env = os.environ.copy()
    env.setdefault("COMPOSE_PROJECT_NAME", "luthien-proxy-dummy-e2e")
    subprocess.run(command, check=True, cwd=PROJECT_ROOT, env=env)


def _wait_for_health(url: str, timeout: float = 180.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            response.raise_for_status()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Service at {url} failed to become healthy")


@pytest.fixture(scope="module", autouse=True)
def dummy_stack() -> Iterator[None]:
    _run_compose("down", "-v", "--remove-orphans")
    _run_compose("up", "-d", "--build")
    _wait_for_health(f"{PROXY_URL}/test")
    _wait_for_health(CONTROL_PLANE_URL)
    try:
        yield
    finally:
        _run_compose("down", "-v", "--remove-orphans")


def test_unified_callback_streaming_chunks_are_openai_format() -> None:
    headers = {"Authorization": f"Bearer {MASTER_KEY}"}
    payload = {
        "model": "dummy-agent",
        "stream": True,
        "messages": [
            {"role": "system", "content": "You are an assistant that echoes text."},
            {"role": "user", "content": "Hello from the unified callback test."},
        ],
    }

    chunks: list[dict] = []
    with httpx.stream("POST", f"{PROXY_URL}/v1/chat/completions", headers=headers, json=payload, timeout=30.0) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                data = line[len("data: ") :]
                if data == "[DONE]":
                    break
                chunks.append(json.loads(data))

    assert chunks, "Expected at least one streaming chunk"
    saw_payload = False
    allowed_keys = {
        "role",
        "content",
        "tool_calls",
        "function_call",
        "provider_specific_fields",
        "refusal",
        "audio",
    }
    for chunk in chunks:
        assert chunk.get("object") == "chat.completion.chunk"
        assert "choices" in chunk and isinstance(chunk["choices"], list)
        delta = chunk["choices"][0]["delta"]
        assert set(delta.keys()).issubset(allowed_keys)
        if delta.get("content") or delta.get("tool_calls"):
            saw_payload = True
    assert saw_payload, "Expected at least one chunk with content or tool_calls"
