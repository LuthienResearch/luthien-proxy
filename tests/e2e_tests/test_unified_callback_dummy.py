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
MASTER_KEY = "sk-luthien-dev-key"
DEFAULT_PORTS = {
    "REDIS_PORT": 46379,
    "POSTGRES_PORT": 45432,
    "DUMMY_LITELLM_PORT": 44400,
    "CONTROL_PLANE_PORT": 48081,
}


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
def dummy_stack() -> Iterator[dict[str, int]]:
    env_overrides = {key: str(DEFAULT_PORTS[key]) for key in DEFAULT_PORTS}
    os.environ.update(env_overrides)

    _run_compose("down", "-v", "--remove-orphans")
    _run_compose("up", "-d", "--build")

    proxy_url = f"http://localhost:{DEFAULT_PORTS['DUMMY_LITELLM_PORT']}"
    control_plane_url = f"http://localhost:{DEFAULT_PORTS['CONTROL_PLANE_PORT']}/health"
    _wait_for_health(f"{proxy_url}/test")
    _wait_for_health(control_plane_url)
    try:
        yield {
            "proxy_port": DEFAULT_PORTS["DUMMY_LITELLM_PORT"],
            "redis_port": DEFAULT_PORTS["REDIS_PORT"],
            "postgres_port": DEFAULT_PORTS["POSTGRES_PORT"],
            "control_plane_port": DEFAULT_PORTS["CONTROL_PLANE_PORT"],
        }
    finally:
        _run_compose("down", "-v", "--remove-orphans")
        for key in env_overrides:
            os.environ.pop(key, None)


def test_unified_callback_streaming_chunks_are_openai_format(dummy_stack: dict[str, int]) -> None:
    proxy_url = f"http://localhost:{dummy_stack['proxy_port']}"
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
    with httpx.stream("POST", f"{proxy_url}/v1/chat/completions", headers=headers, json=payload, timeout=30.0) as resp:
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
