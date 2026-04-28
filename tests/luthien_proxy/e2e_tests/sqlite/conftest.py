"""Start a SQLite-backed gateway for e2e tests — no Docker needed.

Overrides the parent conftest's gateway_url/api_key/admin_api_key fixtures
so test functions transparently hit the in-process SQLite gateway.

Run:  uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/sqlite/ -v --timeout=30
"""

import os

import pytest
from tests.luthien_proxy.e2e_tests.mock_gemini.server import MockGeminiServer
from tests.luthien_proxy.e2e_tests.mock_openai.server import MockOpenAIServer
from tests.luthien_proxy.e2e_tests.sqlite._boot import boot_sqlite_gateway

_API_KEY = "test-sqlite-key"
_ADMIN_API_KEY = "test-sqlite-admin-key"


@pytest.fixture(scope="session")
def _sqlite_booted(mock_anthropic):
    with boot_sqlite_gateway(
        api_key=_API_KEY,
        admin_key=_ADMIN_API_KEY,
        mock_anthropic_url=f"http://localhost:{mock_anthropic.port}",
        tmp_prefix="luthien_sqlite_e2e_",
        thread_name="sqlite-gateway",
    ) as booted:
        yield booted


@pytest.fixture(scope="session")
def sqlite_gateway_url(_sqlite_booted):
    return _sqlite_booted.url


@pytest.fixture(scope="session")
def sqlite_db_path(_sqlite_booted):
    return _sqlite_booted.db_path


# --- Fixture overrides ---
# These override the parent conftest's session-scoped fixtures so all tests
# in this directory (and re-imported tests) hit the SQLite gateway.


@pytest.fixture(scope="session")
def gateway_url(sqlite_gateway_url):
    return sqlite_gateway_url


@pytest.fixture(scope="session")
def api_key():
    return _API_KEY


@pytest.fixture(scope="session")
def admin_api_key():
    return _ADMIN_API_KEY


@pytest.fixture(scope="session")
def mock_openai_server():
    server = MockOpenAIServer()
    server.start()
    old_url = os.environ.get("OPENAI_BASE_URL")
    old_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_BASE_URL"] = server.base_url
    os.environ["OPENAI_API_KEY"] = "mock-openai-key"
    yield server
    server.stop()
    for k, v in (("OPENAI_BASE_URL", old_url), ("OPENAI_API_KEY", old_key)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def mock_gemini_server():
    server = MockGeminiServer()
    server.start()
    old_url = os.environ.get("GEMINI_BASE_URL")
    old_key = os.environ.get("GOOGLE_API_KEY")
    os.environ["GEMINI_BASE_URL"] = server.base_url
    os.environ["GOOGLE_API_KEY"] = "mock-google-key"
    yield server
    server.stop()
    for k, v in (("GEMINI_BASE_URL", old_url), ("GOOGLE_API_KEY", old_key)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
