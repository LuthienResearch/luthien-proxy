"""Tests for the overseer session driver's command building logic."""

import pytest
from scripts.overseer.session_driver import SessionDriver


@pytest.fixture
def driver() -> SessionDriver:
    return SessionDriver(
        container_name="sandbox",
        gateway_url="http://gateway:8000",
        api_key="test-key",
    )


class TestBuildCommand:
    def test_first_turn_has_no_resume(self, driver: SessionDriver):
        cmd = driver._build_command("hello world")
        assert "--resume" not in cmd

    def test_first_turn_includes_required_flags(self, driver: SessionDriver):
        cmd = driver._build_command("hello world")
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--dangerously-skip-permissions" in cmd

    def test_first_turn_prompt_is_last_arg(self, driver: SessionDriver):
        cmd = driver._build_command("do something")
        assert cmd[-1] == "do something"

    def test_resume_command_includes_session_id(self, driver: SessionDriver):
        cmd = driver._build_command("follow up", session_id="abc-123")
        assert "--resume" in cmd
        resume_idx = cmd.index("--resume")
        assert cmd[resume_idx + 1] == "abc-123"

    def test_resume_command_still_has_required_flags(self, driver: SessionDriver):
        cmd = driver._build_command("follow up", session_id="abc-123")
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--verbose" in cmd

    def test_verbose_flag_present(self, driver: SessionDriver):
        cmd = driver._build_command("test prompt")
        assert "--verbose" in cmd
