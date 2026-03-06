"""Tests for overseer CLI argument parsing."""

import pytest
from scripts.overseer.main import parse_args


class TestParseArgs:
    def test_requires_task(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_defaults(self):
        args = parse_args(["--task", "test"])
        assert args.max_turns == 20
        assert args.timeout == 600
        assert args.port == 8080
        assert args.model == "claude-haiku-4-5-20251001"
        assert args.sandbox_model == "claude-haiku-4-5-20251001"
        assert args.gateway_url == "http://gateway:8000"
        assert args.turn_timeout == 600
        assert args.api_key is None
        assert args.auth_token is None
        assert args.compose_project is None

    def test_custom_values(self):
        args = parse_args(
            [
                "--task",
                "build something",
                "--max-turns",
                "5",
                "--timeout",
                "300",
                "--port",
                "9090",
                "--model",
                "claude-sonnet-4-6",
                "--sandbox-model",
                "claude-sonnet-4-6",
                "--turn-timeout",
                "120",
                "--auth-token",
                "my-token",
                "--api-key",
                "my-key",
                "--compose-project",
                "test-project",
            ]
        )
        assert args.task == "build something"
        assert args.max_turns == 5
        assert args.timeout == 300
        assert args.port == 9090
        assert args.model == "claude-sonnet-4-6"
        assert args.sandbox_model == "claude-sonnet-4-6"
        assert args.turn_timeout == 120
        assert args.auth_token == "my-token"
        assert args.api_key == "my-key"
        assert args.compose_project == "test-project"
