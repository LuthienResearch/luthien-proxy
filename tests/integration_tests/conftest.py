# ABOUTME: Pytest configuration for integration tests
# ABOUTME: Re-enables socket access for tests that require external services

"""Pytest configuration for integration tests."""

import pytest


@pytest.fixture(autouse=True)
def _enable_socket_for_integration(socket_enabled):
    """Enable socket access for all integration tests.

    This fixture uses pytest-socket's socket_enabled fixture to re-enable
    network access for tests that need to connect to Redis, PostgreSQL,
    or LLM APIs.
    """
    yield
