"""Tests for version module."""

from luthien_proxy.version import PROXY_VERSION


class TestVersion:
    def test_proxy_version_is_nonempty_string(self):
        assert isinstance(PROXY_VERSION, str)
        assert len(PROXY_VERSION) > 0

    def test_proxy_version_is_pep440(self):
        """hatch-vcs produces PEP 440 versions like '0.1.20.dev2+gabcdef0'."""
        from packaging.version import Version

        Version(PROXY_VERSION)  # raises InvalidVersion if malformed
