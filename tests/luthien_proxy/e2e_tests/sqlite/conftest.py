"""Start a SQLite-backed gateway for e2e tests — no Docker needed.

Overrides the parent conftest's gateway_url/api_key/admin_api_key fixtures
so test functions transparently hit the in-process SQLite gateway.

Run:  uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/sqlite/ -v --timeout=30
"""

import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time

import pytest
import uvicorn

from luthien_proxy.main import create_app
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations

_API_KEY = "test-sqlite-key"
_ADMIN_API_KEY = "test-sqlite-admin-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def sqlite_gateway_url(mock_anthropic):
    """Start a SQLite-backed gateway on a random port. Returns the base URL."""
    port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="luthien_sqlite_e2e_")
    db_path = os.path.join(tmp_dir, "test.db")

    db_pool = DatabasePool(f"sqlite:///{db_path}")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(check_migrations(db_pool))

    # Point LiteLLM at the mock Anthropic server (parent conftest starts it)
    old_env = {}
    mock_port = mock_anthropic.port
    for k, v in {
        "ANTHROPIC_BASE_URL": f"http://localhost:{mock_port}",
        "ANTHROPIC_API_KEY": "mock-key",
    }.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Flush cached settings so create_app() picks up the env vars set above.
    # Without this, get_settings() may return a stale instance that lacks
    # ANTHROPIC_API_KEY, causing credential resolution to fail.
    clear_settings_cache()
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
    thread = threading.Thread(target=server.run, daemon=True, name="sqlite-gateway")
    thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("SQLite gateway did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)
    loop.run_until_complete(db_pool.close())
    loop.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


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
