"""Tests for local_process module."""

import signal
from unittest.mock import MagicMock, patch

import pytest

from luthien_cli.local_process import (
    _parse_env_value,
    gateway_log_path,
    is_gateway_running,
    start_gateway,
    stop_gateway,
)

# === _parse_env_value ===


def test_parse_env_value_plain():
    assert _parse_env_value("hello") == "hello"


def test_parse_env_value_double_quoted():
    assert _parse_env_value('"hello world"') == "hello world"


def test_parse_env_value_single_quoted():
    assert _parse_env_value("'hello world'") == "hello world"


def test_parse_env_value_mismatched_quotes():
    assert _parse_env_value("\"hello'") == "\"hello'"


def test_parse_env_value_empty():
    assert _parse_env_value("") == ""


# === is_gateway_running ===


def test_is_gateway_running_no_pid_file(tmp_path):
    assert is_gateway_running(str(tmp_path)) is None


def test_is_gateway_running_invalid_pid_file(tmp_path):
    (tmp_path / "gateway.pid").write_text("not-a-number")
    assert is_gateway_running(str(tmp_path)) is None


def test_is_gateway_running_stale_pid(tmp_path):
    (tmp_path / "gateway.pid").write_text("99999999")
    with patch("luthien_cli.local_process.os.kill", side_effect=OSError):
        result = is_gateway_running(str(tmp_path))
    assert result is None
    assert not (tmp_path / "gateway.pid").exists()


def test_is_gateway_running_alive(tmp_path):
    (tmp_path / "gateway.pid").write_text("12345")
    with patch("luthien_cli.local_process.os.kill"):
        result = is_gateway_running(str(tmp_path))
    assert result == 12345


# === start_gateway ===


def test_start_gateway_already_running(tmp_path):
    with patch("luthien_cli.local_process.is_gateway_running", return_value=42):
        pid = start_gateway(str(tmp_path))
    assert pid == 42


def test_start_gateway_missing_venv(tmp_path):
    with (
        patch("luthien_cli.local_process.is_gateway_running", return_value=None),
        patch("luthien_cli.local_process._venv_python", return_value="/nonexistent/python"),
    ):
        with pytest.raises(RuntimeError, match="Gateway venv not found"):
            start_gateway(str(tmp_path))


def test_start_gateway_windows_fails(tmp_path):
    with (
        patch("luthien_cli.local_process._is_unix", return_value=False),
        patch("luthien_cli.local_process.is_gateway_running", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="Windows"):
            start_gateway(str(tmp_path))


def test_start_gateway_success(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    mock_proc = MagicMock()
    mock_proc.pid = 54321

    with (
        patch("luthien_cli.local_process.is_gateway_running", return_value=None),
        patch("luthien_cli.local_process._venv_python", return_value=str(venv_python)),
        patch("luthien_cli.local_process.subprocess.Popen", return_value=mock_proc),
        patch("luthien_cli.local_process._is_unix", return_value=True),
    ):
        pid = start_gateway(str(tmp_path), port=9000)

    assert pid == 54321
    assert (tmp_path / "gateway.pid").read_text() == "54321"


def test_start_gateway_loads_env_file(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    (tmp_path / ".env").write_text('KEY1=value1\nKEY2="quoted value"\n# comment\n\n')

    mock_proc = MagicMock()
    mock_proc.pid = 100
    captured_env = {}

    def capture_popen(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return mock_proc

    with (
        patch("luthien_cli.local_process.is_gateway_running", return_value=None),
        patch("luthien_cli.local_process._venv_python", return_value=str(venv_python)),
        patch("luthien_cli.local_process.subprocess.Popen", side_effect=capture_popen),
        patch("luthien_cli.local_process._is_unix", return_value=True),
    ):
        start_gateway(str(tmp_path))

    assert captured_env["KEY1"] == "value1"
    assert captured_env["KEY2"] == "quoted value"


# === stop_gateway ===


def test_stop_gateway_not_running(tmp_path):
    with patch("luthien_cli.local_process.is_gateway_running", return_value=None):
        result = stop_gateway(str(tmp_path))
    assert result is False


def test_stop_gateway_sends_sigterm(tmp_path):
    (tmp_path / "gateway.pid").write_text("12345")

    with (
        patch("luthien_cli.local_process.is_gateway_running", return_value=12345),
        patch("luthien_cli.local_process.os.kill") as mock_kill,
    ):
        # After SIGTERM, process exits on first check
        mock_kill.side_effect = [None, OSError("no such process")]
        result = stop_gateway(str(tmp_path))

    assert result is True
    mock_kill.assert_any_call(12345, signal.SIGTERM)
    assert not (tmp_path / "gateway.pid").exists()


def test_stop_gateway_with_console(tmp_path):
    console = MagicMock()
    with (
        patch("luthien_cli.local_process.is_gateway_running", return_value=12345),
        patch("luthien_cli.local_process.os.kill", side_effect=[None, OSError]),
    ):
        stop_gateway(str(tmp_path), console=console)
    console.print.assert_called()


# === gateway_log_path ===


def test_gateway_log_path(tmp_path):
    path = gateway_log_path(str(tmp_path))
    assert path.name == "gateway.log"
    assert str(tmp_path) in str(path)
