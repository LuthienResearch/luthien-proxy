"""Start a SQLite-backed gateway for mock e2e tests.

This conftest patches the parent conftest's GATEWAY_URL/API_KEY so the
existing mock e2e test files work without modification. To run:

    uv run pytest tests/e2e_tests/sqlite/ -v --timeout=30

The test files in this directory import from the mock e2e tests and re-run
them against the SQLite gateway.
"""

import asyncio
import os
import socket
import tempfile
import threading
import time

import pytest
import uvicorn

from luthien_proxy.main import create_app
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations

_API_KEY = "test-sqlite-key"
_ADMIN_API_KEY = "test-sqlite-admin-key"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def sqlite_gateway_url():
    """Start a SQLite-backed gateway on a random port. Returns the base URL."""
    port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="luthien_sqlite_e2e_")
    db_path = os.path.join(tmp_dir, "test.db")

    db_pool = DatabasePool(f"sqlite:///{db_path}")

    # Apply schema before server starts
    loop = asyncio.new_event_loop()
    loop.run_until_complete(check_migrations(db_pool))

    # Point LiteLLM at the mock Anthropic server (parent conftest starts it on 18888)
    old_env = {}
    for k, v in {"ANTHROPIC_BASE_URL": "http://localhost:18888", "ANTHROPIC_API_KEY": "mock-key"}.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

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

    url = f"http://127.0.0.1:{port}"

    # Patch parent conftest so helpers like policy_context() use the right URL/keys
    import tests.luthien_proxy.e2e_tests.conftest as parent

    orig = (parent.GATEWAY_URL, parent.API_KEY, parent.ADMIN_API_KEY)
    parent.GATEWAY_URL = url
    parent.API_KEY = _API_KEY
    parent.ADMIN_API_KEY = _ADMIN_API_KEY

    # Test modules do `from ... import GATEWAY_URL` which creates local bindings,
    # and compute `_HEADERS = {"Authorization": f"Bearer {API_KEY}"}` at import
    # time. We must patch all of these derived values too.
    import tests.luthien_proxy.e2e_tests.test_mock_admin_api as m_admin
    import tests.luthien_proxy.e2e_tests.test_mock_basic as m_basic
    import tests.luthien_proxy.e2e_tests.test_mock_error_handling as m_errors
    import tests.luthien_proxy.e2e_tests.test_mock_onboarding_policy as m_onboarding
    import tests.luthien_proxy.e2e_tests.test_mock_openai_and_tool_use as m_openai
    import tests.luthien_proxy.e2e_tests.test_mock_policies as m_policies
    import tests.luthien_proxy.e2e_tests.test_mock_policy_management as m_polmgmt
    import tests.luthien_proxy.e2e_tests.test_mock_request_forwarding as m_fwd
    import tests.luthien_proxy.e2e_tests.test_mock_session_history as m_sessions
    import tests.luthien_proxy.e2e_tests.test_mock_special_chars as m_chars
    import tests.luthien_proxy.e2e_tests.test_mock_streaming_structure as m_stream

    _test_modules = [
        m_basic,
        m_errors,
        m_admin,
        m_polmgmt,
        m_policies,
        m_fwd,
        m_sessions,
        m_stream,
        m_openai,
        m_chars,
        m_onboarding,
    ]

    # Attributes to patch: the imported constants + derived header dicts
    _attrs = ("GATEWAY_URL", "API_KEY", "ADMIN_API_KEY", "_HEADERS", "_ADMIN_HEADERS")
    _new_values = {
        "GATEWAY_URL": url,
        "API_KEY": _API_KEY,
        "ADMIN_API_KEY": _ADMIN_API_KEY,
        "_HEADERS": {"Authorization": f"Bearer {_API_KEY}"},
        "_ADMIN_HEADERS": {"Authorization": f"Bearer {_ADMIN_API_KEY}"},
    }

    _orig_values = []
    for mod in _test_modules:
        saved = {attr: getattr(mod, attr, None) for attr in _attrs}
        _orig_values.append(saved)
        for attr, new_val in _new_values.items():
            if hasattr(mod, attr):
                setattr(mod, attr, new_val)

    yield url

    # Restore everything
    for mod, saved in zip(_test_modules, _orig_values):
        for attr, val in saved.items():
            if val is not None:
                setattr(mod, attr, val)
    parent.GATEWAY_URL, parent.API_KEY, parent.ADMIN_API_KEY = orig
    server.should_exit = True
    thread.join(timeout=5)
    loop.run_until_complete(db_pool.close())
    loop.close()
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _ensure_sqlite_gateway(sqlite_gateway_url):
    """Autouse: ensure gateway is running for every test in this directory."""
