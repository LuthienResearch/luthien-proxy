"""Tests for config command."""

from unittest.mock import patch

from click.testing import CliRunner
from luthien_cli.main import cli


def test_config_show_displays_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\napi_key = "sk-test"\n')
    runner = CliRunner()
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "localhost:9000" in result.output


def test_config_set_updates_value(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    runner = CliRunner()
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "set", "gateway.url", "http://remote:9000"])
        assert result.exit_code == 0
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "show"])
        assert "remote:9000" in result.output


def test_config_set_rejects_unknown_key(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    runner = CliRunner()
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "set", "bogus.key", "value"])
        assert result.exit_code != 0
        assert "unknown" in result.output.lower()


def test_config_show_masks_api_key(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\napi_key = "sk-very-long-secret-key"\n')
    runner = CliRunner()
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "sk-very-long-secret-key" not in result.output
        assert "sk-v" in result.output
