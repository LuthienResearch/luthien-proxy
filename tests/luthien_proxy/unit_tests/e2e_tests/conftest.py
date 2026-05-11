"""Allow network sockets for e2e-infra unit tests.

The parent ``unit_tests/conftest.py`` autouses ``_block_network_sockets``
to keep real test code from accidentally hitting Redis/Postgres/LLM
APIs. The tests in this subdirectory exercise the e2e mock-server
infrastructure itself, which legitimately needs to allocate and bind
local TCP sockets to verify port-handling. We override the autouse
fixture here with a no-op so those tests can run.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _block_network_sockets():
    """Override the parent socket-blocker for this subtree only."""
    yield
