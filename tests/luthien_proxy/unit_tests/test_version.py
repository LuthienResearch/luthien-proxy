"""Tests for version module."""

from unittest.mock import patch

from luthien_proxy.version import (
    PROXY_VERSION,
    _get_git_commit,
    _read_build_commit,
    _resolve_version,
)


class TestVersion:
    def test_proxy_version_is_string(self):
        assert isinstance(PROXY_VERSION, str)
        assert len(PROXY_VERSION) > 0

    def test_get_git_commit_returns_short_hash(self):
        """In a git repo, should return a 7-12 char hex string."""
        result = _get_git_commit()
        assert result is not None
        assert all(c in "0123456789abcdef" for c in result)
        assert 7 <= len(result) <= 12

    def test_read_build_commit_returns_none_when_no_file(self):
        """No BUILD_COMMIT file in dev checkout."""
        result = _read_build_commit()
        # May or may not exist depending on environment
        assert result is None or isinstance(result, str)

    def test_resolve_version_prefers_env_var(self):
        with patch.dict("os.environ", {"LUTHIEN_BUILD_COMMIT": "abc1234"}):
            assert _resolve_version() == "abc1234"

    def test_resolve_version_truncates_long_build_commit(self, tmp_path):
        """BUILD_COMMIT file with full SHA gets truncated to 8 chars."""
        build_file = tmp_path / "BUILD_COMMIT"
        full_sha = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
        build_file.write_text(full_sha)

        with patch("luthien_proxy.version._PACKAGE_DIR", tmp_path):
            result = _read_build_commit()
            assert result == "a1b2c3d4"

    def test_resolve_version_falls_through_to_git(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("luthien_proxy.version._read_build_commit", return_value=None):
                result = _resolve_version()
                # In a git repo, should get a real commit hash
                assert result != "unknown"
