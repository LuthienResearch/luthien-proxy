"""Integration tests for MockAnthropicServer port allocation.

These tests bind real local TCP sockets and start aiohttp servers in
background threads, so they live in ``integration_tests/`` rather than
``unit_tests/`` (where an autouse fixture blocks network sockets).
"""

from __future__ import annotations

import socket

import pytest
from tests.luthien_proxy.e2e_tests.mock_anthropic.server import MockAnthropicServer

pytestmark = pytest.mark.integration


def test_default_port_is_auto_allocated():
    """Constructing without an explicit port allocates a free OS port."""
    server = MockAnthropicServer()
    assert server.port > 0

    # Verify the chosen port is actually bindable.
    with socket.socket() as s:
        s.bind(("", server.port))


def test_explicit_port_is_respected():
    """Passing a specific port keeps that exact value."""
    # Reserve a port to use as our test value, then close the socket so
    # the server can bind it itself.
    with socket.socket() as s:
        s.bind(("", 0))
        chosen = s.getsockname()[1]

    server = MockAnthropicServer(port=chosen)
    assert server.port == chosen


def test_port_zero_means_auto_allocate():
    """``port=0`` is the explicit form of the auto-allocate request."""
    server = MockAnthropicServer(port=0)
    assert server.port > 0


def test_two_servers_get_distinct_ports():
    """Independent default-constructed servers do not collide on a port."""
    a = MockAnthropicServer()
    b = MockAnthropicServer()
    assert a.port != b.port


def test_two_started_servers_coexist():
    """Regression for the original bug: two running mocks on one machine.

    Before auto-allocation, default-port servers fought over 18888 and
    the second `start()` raised ``OSError: [Errno 48] address already
    in use`` mid-suite.
    """
    a = MockAnthropicServer()
    b = MockAnthropicServer()
    a.start()
    try:
        b.start()
        try:
            assert a.port != b.port
        finally:
            b.stop()
    finally:
        a.stop()


def test_base_url_reflects_chosen_port():
    """``base_url`` is built from the actual bound port."""
    server = MockAnthropicServer()
    assert server.base_url == f"http://localhost:{server.port}"
