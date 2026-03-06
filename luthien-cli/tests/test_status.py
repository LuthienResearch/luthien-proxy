"""Tests for status command."""

from unittest.mock import patch

from click.testing import CliRunner
from luthien_cli.main import cli


def test_status_shows_healthy_gateway():
    runner = CliRunner()
    with patch("luthien_cli.commands.status.make_client") as mock_client:
        client = mock_client.return_value
        client.base_url = "http://localhost:8000"
        client.health.return_value = {"status": "healthy", "version": "2.0.0"}
        client.get_current_policy.return_value = {
            "policy": "NoOpPolicy",
            "class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "enabled_at": "2026-03-03T10:00:00",
            "enabled_by": "api",
            "config": {},
        }
        client.get_auth_config.return_value = {
            "auth_mode": "both",
            "validate_credentials": True,
            "valid_cache_ttl_seconds": 300,
            "invalid_cache_ttl_seconds": 60,
        }
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "healthy" in result.output
        assert "NoOpPolicy" in result.output


def test_status_shows_unreachable_gateway():
    runner = CliRunner()
    with patch("luthien_cli.commands.status.make_client") as mock_client:
        from luthien_cli.gateway_client import GatewayError

        client = mock_client.return_value
        client.health.side_effect = GatewayError("Cannot connect")
        result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0 or "Cannot connect" in result.output
