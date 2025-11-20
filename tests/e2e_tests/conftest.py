# ABOUTME: Pytest configuration for end-to-end tests
# ABOUTME: Re-enables socket access for tests that require the full control plane

"""Pytest configuration for end-to-end tests."""

import pytest


@pytest.fixture(autouse=True)
def _enable_socket_for_e2e(socket_enabled):
    """Enable socket access for all e2e tests.

    This fixture uses pytest-socket's socket_enabled fixture to re-enable
    network access for tests that require the full control plane with
    Redis, PostgreSQL, and LLM APIs.
    """
    yield
