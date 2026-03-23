"""Integration test — verify all commands are registered and --help works."""

from click.testing import CliRunner

from luthien_cli.main import cli


def test_all_commands_registered():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ["status", "claude", "up", "down", "logs", "config"]:
        assert cmd in result.output, f"Command '{cmd}' not in help output"


def test_each_command_has_help():
    runner = CliRunner()
    for cmd in ["status", "claude", "up", "down", "logs", "config"]:
        result = runner.invoke(cli, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed"


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output
