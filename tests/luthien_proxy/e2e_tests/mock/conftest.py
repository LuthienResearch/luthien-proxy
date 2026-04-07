"""In-process gateway fixture for mock_e2e tests using MockAnthropicClient.

Overrides the parent conftest's gateway_url / api_key / admin_api_key / mock_anthropic
fixtures to start an in-process gateway with MockAnthropicClient injected directly —
no subprocess, no port allocation for the main LLM backend.

A companion MockAnthropicServer also runs for tests that inspect HTTP headers on
judge calls (test_mock_simple_llm_passthrough_auth.py). The server shares its
queue and request-recording state with MockAnthropicClient.
"""

import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Generator

import pytest
import tests.luthien_proxy.e2e_tests.conftest as _parent_conftest
import uvicorn
from litellm import Message
from litellm.types.utils import Choices, ModelResponse
from tests.luthien_proxy.e2e_tests.mock_anthropic.client import MockAnthropicClient
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import MockResponse
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

import luthien_proxy.llm.judge_client as _judge_client
import luthien_proxy.policies.simple_llm_utils as _simple_llm_utils
from luthien_proxy.llm import anthropic_client_cache
from luthien_proxy.main import create_app
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations

# MOCK_HOST defaults to "host.docker.internal" (Docker mode) but the companion
# HTTP server always listens on localhost. Override before test files are imported.
_parent_conftest.MOCK_HOST = "localhost"

_API_KEY = "test-mock-e2e-key"
_ADMIN_API_KEY = "test-mock-e2e-admin-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_anthropic() -> Generator[MockAnthropicClient, None, None]:
    client = MockAnthropicClient()

    # Start a companion HTTP server that shares the client's queue and state.
    # Required for passthrough-auth tests that inspect HTTP request headers.
    http_server = MockAnthropicServer(port=_free_port())
    http_server._queue = client._queue  # type: ignore[assignment]

    def _shared_record(body: dict, headers: dict | None = None) -> None:
        with client._lock:
            client._received_requests.append(body)
            client._received_headers.append(headers or {})

    http_server._record_request = _shared_record  # type: ignore[assignment]
    http_server.start()

    client.port = http_server.port  # type: ignore[attr-defined]

    try:
        yield client
    finally:
        http_server.stop()


@pytest.fixture(scope="session")
def gateway_url(mock_anthropic: MockAnthropicClient) -> Generator[str, None, None]:
    port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="luthien_mock_e2e_")
    db_path = os.path.join(tmp_dir, "test.db")

    db_pool = DatabasePool(f"sqlite:///{db_path}")
    loop = asyncio.new_event_loop()
    loop.run_until_complete(check_migrations(db_pool))

    app = create_app(
        api_key=_API_KEY,
        admin_key=_ADMIN_API_KEY,
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path="config/policy_config.yaml",
        policy_source="db-fallback-file",
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="mock-gateway")
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Mock gateway did not start within 10s")

    # Inject after startup: app.state.dependencies is created during lifespan startup.
    app.state.dependencies.anthropic_client = mock_anthropic  # type: ignore[assignment]

    # Patch credential cache so passthrough auth also routes through MockAnthropicClient.
    # Without this, passthrough requests create real AnthropicClient instances that
    # make live API calls to Anthropic.
    _original_get_client = anthropic_client_cache.get_client

    async def _mock_get_client(token: str, auth_type: str, base_url: str | None = None):
        return mock_anthropic

    anthropic_client_cache.get_client = _mock_get_client  # type: ignore[assignment]

    # Patch LiteLLM acompletion so judge LLM calls (SimpleLLMPolicy, NoYappingPolicy, etc.)
    # also dequeue from MockAnthropicClient instead of hitting the real Anthropic API.
    # When api_base is explicitly set (passthrough-auth tests), delegate to the original
    # acompletion so the call reaches the companion HTTP server and headers are inspectable.
    _original_slu_acompletion = _simple_llm_utils.acompletion
    _original_jc_acompletion = _judge_client.acompletion

    async def _mock_acompletion(*args, **kwargs):  # type: ignore[misc]
        if kwargs.get("api_base"):
            return await _original_slu_acompletion(*args, **kwargs)
        mock = mock_anthropic._dequeue()
        text = mock.text if isinstance(mock, MockResponse) else "{}"
        with mock_anthropic._lock:
            mock_anthropic._received_requests.append({"messages": kwargs.get("messages", [])})
            headers: dict[str, str] = {}
            if "api_key" in kwargs:
                headers["x-api-key"] = str(kwargs["api_key"])
            if "extra_headers" in kwargs:
                headers.update(kwargs["extra_headers"])
            mock_anthropic._received_headers.append(headers)
        return ModelResponse(
            id=f"mock-{id(mock)}",
            choices=[Choices(message=Message(role="assistant", content=text), finish_reason="stop", index=0)],
            model=kwargs.get("model", "mock"),
        )

    _simple_llm_utils.acompletion = _mock_acompletion  # type: ignore[assignment]
    _judge_client.acompletion = _mock_acompletion  # type: ignore[assignment]

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        anthropic_client_cache.get_client = _original_get_client
        _simple_llm_utils.acompletion = _original_slu_acompletion
        _judge_client.acompletion = _original_jc_acompletion
        server.should_exit = True
        thread.join(timeout=5)
        loop.run_until_complete(db_pool.close())
        loop.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def api_key() -> str:
    return _API_KEY


@pytest.fixture(scope="session")
def admin_api_key() -> str:
    return _ADMIN_API_KEY
