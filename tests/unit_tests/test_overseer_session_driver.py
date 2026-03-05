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


@pytest.fixture
def oauth_driver() -> SessionDriver:
    return SessionDriver(
        container_name="sandbox",
        gateway_url="http://gateway:8000",
        auth_token="oauth-token-123",
    )


class TestInit:
    def test_requires_api_key_or_auth_token(self):
        with pytest.raises(ValueError, match="Either api_key or auth_token"):
            SessionDriver(
                container_name="sandbox",
                gateway_url="http://gateway:8000",
            )

    def test_api_key_only(self):
        d = SessionDriver(
            container_name="sandbox",
            gateway_url="http://gateway:8000",
            api_key="key",
        )
        assert d.api_key == "key"
        assert d.auth_token is None

    def test_auth_token_only(self):
        d = SessionDriver(
            container_name="sandbox",
            gateway_url="http://gateway:8000",
            auth_token="token",
        )
        assert d.auth_token == "token"
        assert d.api_key is None

    def test_both_api_key_and_auth_token(self):
        d = SessionDriver(
            container_name="sandbox",
            gateway_url="http://gateway:8000",
            api_key="key",
            auth_token="token",
        )
        assert d.api_key == "key"
        assert d.auth_token == "token"


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

    def test_no_model_flag_by_default(self, driver: SessionDriver):
        cmd = driver._build_command("test prompt")
        assert "--model" not in cmd

    def test_model_flag_when_set(self):
        driver = SessionDriver(
            container_name="sandbox",
            gateway_url="http://gateway:8000",
            api_key="test-key",
            model="claude-haiku-4-5-20251001",
        )
        cmd = driver._build_command("test prompt")
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "claude-haiku-4-5-20251001"
