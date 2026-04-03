"""Tests for URL sanitization utilities."""

from __future__ import annotations

import pytest

from luthien_proxy.utils.url import sanitize_url_for_logging


class TestSanitizeUrlForLogging:
    """Tests for sanitize_url_for_logging."""

    def test_strips_password_from_redis_url(self) -> None:
        url = "redis://user:s3cret@redis-host:6379/0"
        result = sanitize_url_for_logging(url)
        assert "s3cret" not in result
        assert "user:***@redis-host:6379/0" in result

    def test_strips_password_from_postgres_url(self) -> None:
        url = "postgresql://admin:hunter2@db.example.com:5432/mydb"
        result = sanitize_url_for_logging(url)
        assert "hunter2" not in result
        assert "admin:***@db.example.com:5432/mydb" in result

    def test_no_password_returns_unchanged(self) -> None:
        url = "redis://redis-host:6379/0"
        assert sanitize_url_for_logging(url) == url

    def test_username_only_returns_unchanged(self) -> None:
        url = "redis://user@redis-host:6379/0"
        assert sanitize_url_for_logging(url) == url

    def test_preserves_scheme(self) -> None:
        url = "rediss://user:password@redis-host:6380/0"
        result = sanitize_url_for_logging(url)
        assert result.startswith("rediss://")
        assert "password" not in result

    def test_preserves_path_and_query(self) -> None:
        url = "redis://user:pass@host:6379/0?timeout=5"
        result = sanitize_url_for_logging(url)
        assert "pass" not in result
        assert "/0?timeout=5" in result

    def test_no_port(self) -> None:
        url = "redis://user:pass@host/0"
        result = sanitize_url_for_logging(url)
        assert "pass" not in result
        assert "user:***@host/0" in result

    def test_empty_string(self) -> None:
        assert sanitize_url_for_logging("") == ""

    @pytest.mark.parametrize("url", ["not-a-url", "localhost:6379", "/just/a/path"])
    def test_non_url_strings_returned_unchanged(self, url: str) -> None:
        assert sanitize_url_for_logging(url) == url
