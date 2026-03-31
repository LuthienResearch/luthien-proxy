# ABOUTME: Unit tests for --local flag (dockerless startup mode)
# ABOUTME: Tests env var defaulting, ephemeral key generation, and no-override semantics

"""Tests for local mode configuration (--local flag)."""

import os

from luthien_proxy.main import configure_local_mode


class TestConfigureLocalMode:
    """Test configure_local_mode sets correct env var defaults."""

    def test_sets_database_url_to_sqlite(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        configure_local_mode()
        expected = f"sqlite:///{os.path.expanduser('~')}/.luthien/local.db"
        assert os.environ["DATABASE_URL"] == expected

    def test_sets_redis_url_empty(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        configure_local_mode()
        assert os.environ["REDIS_URL"] == ""

    def test_sets_policy_config(self, monkeypatch):
        monkeypatch.delenv("POLICY_CONFIG", raising=False)
        configure_local_mode()
        assert os.environ["POLICY_CONFIG"] == "config/policy_config.yaml"

    def test_sets_policy_source_to_file(self, monkeypatch):
        monkeypatch.delenv("POLICY_SOURCE", raising=False)
        configure_local_mode()
        assert os.environ["POLICY_SOURCE"] == "file"

    def test_does_not_set_proxy_api_key(self, monkeypatch):
        monkeypatch.delenv("PROXY_API_KEY", raising=False)
        configure_local_mode()
        assert "PROXY_API_KEY" not in os.environ

    def test_does_not_set_admin_api_key(self, monkeypatch):
        monkeypatch.delenv("ADMIN_API_KEY", raising=False)
        configure_local_mode()
        assert "ADMIN_API_KEY" not in os.environ

    def test_force_overrides_existing_database_url(self, monkeypatch):
        """Infrastructure vars are force-set because litellm's dotenv pollutes os.environ."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://custom:5432/db")
        configure_local_mode()
        assert os.environ["DATABASE_URL"].startswith("sqlite:///")
        assert os.environ["DATABASE_URL"].endswith("/.luthien/local.db")
