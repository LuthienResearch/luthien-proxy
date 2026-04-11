"""Start a SQLite-backed gateway for e2e tests — no Docker needed.

Overrides the parent conftest's gateway_url/api_key/admin_api_key fixtures
so test functions transparently hit the in-process SQLite gateway.

Run:  uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/sqlite/ -v --timeout=30
"""

import pytest
from tests.luthien_proxy.e2e_tests.sqlite._boot import boot_sqlite_gateway

_API_KEY = "test-sqlite-key"
_ADMIN_API_KEY = "test-sqlite-admin-key"


@pytest.fixture(scope="session")
def sqlite_gateway_url(mock_anthropic):
    """Start a SQLite-backed gateway on a random port. Returns the base URL."""
    with boot_sqlite_gateway(
        api_key=_API_KEY,
        admin_key=_ADMIN_API_KEY,
        mock_anthropic_url=f"http://localhost:{mock_anthropic.port}",
        tmp_prefix="luthien_sqlite_e2e_",
        thread_name="sqlite-gateway",
    ) as url:
        yield url


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
