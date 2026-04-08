"""Tests for version module."""

import pytest
from packaging.version import Version

from luthien_proxy.version import (
    PROXY_DISPLAY_VERSION,
    PROXY_VERSION,
    _short_version,
)


class TestVersion:
    def test_proxy_version_is_nonempty_string(self):
        assert isinstance(PROXY_VERSION, str)
        assert len(PROXY_VERSION) > 0
        assert PROXY_VERSION != "unknown"

    def test_proxy_version_is_pep440(self):
        """hatch-vcs produces PEP 440 versions like '0.1.20.dev2+gabcdef0'."""
        Version(PROXY_VERSION)  # raises InvalidVersion if malformed

    def test_display_version_is_nonempty(self):
        assert isinstance(PROXY_DISPLAY_VERSION, str)
        assert len(PROXY_DISPLAY_VERSION) > 0


class TestShortVersion:
    @pytest.mark.parametrize(
        "full, expected",
        [
            ("1.0.0", "1.0.0"),  # tagged release
            ("0.0.0+abc1234", "abc1234"),  # Docker build
            ("0.1.20.dev2+g64a517c2.d20260407", "64a517c2"),  # dev build
            ("0.0.0+unknown", "unknown"),  # fallback
        ],
    )
    def test_short_version(self, full: str, expected: str):
        assert _short_version(full) == expected
