"""Proxy-specific test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the settings cache before each test.

    This ensures that tests that modify environment variables get fresh
    settings instances instead of stale cached values.
    """
    from luthien_proxy.settings import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()
