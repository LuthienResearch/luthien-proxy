"""Tests for hackathon command helpers."""

import io
import stat
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner
from rich.console import Console

from luthien_cli.commands.hackathon import (
    POLICY_CHOICES,
    _checkout_proxy_ref,
    _clone_repo,
    _generate_key,
    _install_deps,
    _parse_env_value,
    _pick_policy,
    _read_existing_keys,
    _write_env,
    _write_policy_config,
    hackathon,
)


class TestGenerateKey:
    """Tests for _generate_key()."""

    def test_generate_key_with_prefix(self):
        """Verify prefix is present and key has correct format."""
        key = _generate_key("sk-luthien")
        assert key.startswith("sk-luthien-")
        # Key should be longer than just the prefix
        assert len(key) > len("sk-luthien-")

    def test_generate_key_different_each_time(self):
        """Verify key is unique on each call."""
        key1 = _generate_key("admin")
        key2 = _generate_key("admin")
        assert key1 != key2
        assert key1.startswith("admin-")
        assert key2.startswith("admin-")

    def test_generate_key_url_safe(self):
        """Verify token is URL-safe."""
        key = _generate_key("test")
        # URL-safe means no slashes, plus signs, equals signs
        token_part = key.split("-", 1)[1]
        assert "/" not in token_part
        assert "+" not in token_part
        assert "=" not in token_part


class TestParseEnvValue:
    """Tests for _parse_env_value()."""

    def test_double_quoted_value(self):
        """Strip double quotes from value."""
        result = _parse_env_value('"hello world"')
        assert result == "hello world"

    def test_single_quoted_value(self):
        """Strip single quotes from value."""
        result = _parse_env_value("'hello world'")
        assert result == "hello world"

    def test_unquoted_value(self):
        """Return unquoted value as is."""
        result = _parse_env_value("hello_world")
        assert result == "hello_world"

    def test_empty_string(self):
        """Return empty string as is."""
        result = _parse_env_value("")
        assert result == ""

    def test_value_with_equals_sign(self):
        """Handle value containing equals sign."""
        result = _parse_env_value("key=value")
        assert result == "key=value"

    def test_quoted_value_with_equals(self):
        """Strip quotes from value containing equals sign."""
        result = _parse_env_value('"key=value"')
        assert result == "key=value"

    def test_mismatched_quotes_not_stripped(self):
        """Don't strip mismatched quotes."""
        result = _parse_env_value("\"value'")
        assert result == "\"value'"

    def test_single_quote_only(self):
        """Don't strip single quote when not at end."""
        result = _parse_env_value("'value")
        assert result == "'value"

    def test_quoted_empty_string(self):
        """Strip quotes from empty quoted string."""
        result = _parse_env_value('""')
        assert result == ""


class TestWriteEnv:
    """Tests for _write_env()."""

    def test_write_env_creates_file(self, tmp_path):
        """Verify .env file is created with correct content."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        _write_env(repo_path, "admin-test-key", 9000)

        env_file = repo_path / ".env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "PROXY_API_KEY" not in content
        assert "ADMIN_API_KEY=admin-test-key" in content
        assert "GATEWAY_PORT=9000" in content

    def test_write_env_file_permissions(self, tmp_path):
        """Verify .env file has 0o600 permissions."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        _write_env(repo_path, "admin-test-key", 9000)

        env_file = repo_path / ".env"
        file_stat = env_file.stat()
        file_mode = stat.S_IMODE(file_stat.st_mode)
        assert file_mode == 0o600

    def test_write_env_contains_all_keys(self, tmp_path):
        """Verify all expected keys are present."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        _write_env(repo_path, "admin-admin", 8888)

        env_file = repo_path / ".env"
        content = env_file.read_text()
        expected_keys = [
            "DATABASE_URL",
            "ADMIN_API_KEY",
            "POLICY_SOURCE",
            "POLICY_CONFIG",
            "AUTH_MODE",
            "OTEL_ENABLED",
            "USAGE_TELEMETRY",
            "GATEWAY_PORT",
        ]
        for key in expected_keys:
            assert f"{key}=" in content
        assert "PROXY_API_KEY" not in content

    def test_write_env_sqlite_path(self, tmp_path):
        """Verify SQLite path is correctly set."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        _write_env(repo_path, "admin-test", 8000)

        env_file = repo_path / ".env"
        content = env_file.read_text()
        expected_db_path = str(repo_path / "luthien.db")
        assert f"sqlite:///{expected_db_path}" in content

    def test_write_env_policy_config_path(self, tmp_path):
        """Verify policy config path is correctly set."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        _write_env(repo_path, "admin-test", 8000)

        env_file = repo_path / ".env"
        content = env_file.read_text()
        expected_policy_path = str(repo_path / "config" / "policy_config.yaml")
        assert f"POLICY_CONFIG={expected_policy_path}" in content


class TestWritePolicyConfig:
    """Tests for _write_policy_config()."""

    def test_write_policy_config_creates_file(self, tmp_path):
        """Verify policy_config.yaml is created."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        policy_class_ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        _write_policy_config(repo_path, policy_class_ref, "http://localhost:8000")

        config_file = repo_path / "config" / "policy_config.yaml"
        assert config_file.exists()

    def test_write_policy_config_yaml_structure(self, tmp_path):
        """Verify YAML output has correct structure."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        policy_class_ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        _write_policy_config(repo_path, policy_class_ref, "http://localhost:8000")

        config_file = repo_path / "config" / "policy_config.yaml"
        data = yaml.safe_load(config_file.read_text())
        assert "policy" in data
        assert "class" in data["policy"]
        assert "config" in data["policy"]
        assert data["policy"]["class"] == policy_class_ref

    def test_write_policy_config_gateway_url_for_hackathon(self, tmp_path):
        """Verify gateway_url is included for hackathon_onboarding_policy."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        policy_class_ref = "luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy"
        gateway_url = "http://localhost:9000"
        _write_policy_config(repo_path, policy_class_ref, gateway_url)

        config_file = repo_path / "config" / "policy_config.yaml"
        data = yaml.safe_load(config_file.read_text())
        assert data["policy"]["config"]["gateway_url"] == gateway_url

    def test_write_policy_config_no_gateway_url_for_other_policies(self, tmp_path):
        """Verify gateway_url is NOT included for non-hackathon policies."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        policy_class_ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        gateway_url = "http://localhost:9000"
        _write_policy_config(repo_path, policy_class_ref, gateway_url)

        config_file = repo_path / "config" / "policy_config.yaml"
        data = yaml.safe_load(config_file.read_text())
        assert data["policy"]["config"] == {}

    def test_write_policy_config_creates_config_directory(self, tmp_path):
        """Verify config directory is created if it doesn't exist."""
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # config dir should not exist yet
        assert not (repo_path / "config").exists()

        policy_class_ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        _write_policy_config(repo_path, policy_class_ref, "http://localhost:8000")

        assert (repo_path / "config").exists()


class TestPickPolicy:
    """Tests for _pick_policy()."""

    def test_pick_policy_with_yes_flag(self):
        """Verify default policy is returned when yes=True."""
        console_output = io.StringIO()
        console = Console(file=console_output)

        policy_class_ref, policy_name = _pick_policy(console, yes=True)

        # Should return the default policy (choice "1")
        default_choice = POLICY_CHOICES["1"]
        assert policy_class_ref == default_choice[1]
        assert policy_name == default_choice[0]

    def test_pick_policy_interactive_default_choice(self):
        """Verify default is returned when user enters nothing."""
        console_output = io.StringIO()
        console = Console(file=console_output)

        with patch.object(console, "input", return_value=""):
            policy_class_ref, policy_name = _pick_policy(console, yes=False)

        default_choice = POLICY_CHOICES["1"]
        assert policy_class_ref == default_choice[1]
        assert policy_name == default_choice[0]

    def test_pick_policy_interactive_valid_choice(self):
        """Verify specific choice is returned when user enters valid input."""
        console_output = io.StringIO()
        console = Console(file=console_output)

        with patch.object(console, "input", return_value="2"):
            policy_class_ref, policy_name = _pick_policy(console, yes=False)

        choice_2 = POLICY_CHOICES["2"]
        assert policy_class_ref == choice_2[1]
        assert policy_name == choice_2[0]

    def test_pick_policy_interactive_invalid_choice(self):
        """Verify default is used for invalid choice."""
        console_output = io.StringIO()
        console = Console(file=console_output)

        with patch.object(console, "input", return_value="99"):
            policy_class_ref, policy_name = _pick_policy(console, yes=False)

        default_choice = POLICY_CHOICES["1"]
        assert policy_class_ref == default_choice[1]
        assert policy_name == default_choice[0]

    def test_pick_policy_returns_tuple_of_two_strings(self):
        """Verify return value is a tuple of two strings."""
        console_output = io.StringIO()
        console = Console(file=console_output)

        result = _pick_policy(console, yes=True)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)


class TestReadExistingKeys:
    """Tests for _read_existing_keys()."""

    def test_no_env_file(self, tmp_path):
        env_path = tmp_path / ".env"
        proxy, admin = _read_existing_keys(env_path)
        assert proxy is None
        assert admin is None

    def test_reads_existing_keys(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("PROXY_API_KEY=sk-existing\nADMIN_API_KEY=admin-existing\n")
        proxy, admin = _read_existing_keys(env_path)
        assert proxy == "sk-existing"
        assert admin == "admin-existing"

    def test_reads_quoted_keys(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("PROXY_API_KEY=\"sk-quoted\"\nADMIN_API_KEY='admin-quoted'\n")
        proxy, admin = _read_existing_keys(env_path)
        assert proxy == "sk-quoted"
        assert admin == "admin-quoted"


class TestWriteEnvKeyPreservation:
    """Tests for _write_env key preservation on re-runs."""

    def test_preserves_existing_admin_key(self, tmp_path):
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # First run
        _write_env(repo_path, "admin-first", 8000)
        # Second run with different key
        admin = _write_env(repo_path, "admin-second", 9000)
        assert admin == "admin-first"

    def test_uses_new_key_on_first_run(self, tmp_path):
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        admin = _write_env(repo_path, "admin-new", 8000)
        assert admin == "admin-new"

    def test_returns_actual_key_used(self, tmp_path):
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        result = _write_env(repo_path, "admin-test", 8000)
        assert isinstance(result, str)


class TestCloneRepo:
    """Tests for _clone_repo error paths."""

    def test_existing_dir_not_git_repo(self, tmp_path):
        console = Console(file=io.StringIO())
        clone_path = tmp_path / "not-a-repo"
        clone_path.mkdir()
        assert _clone_repo(console, clone_path) is False

    def test_existing_git_repo_reused(self, tmp_path):
        console = Console(file=io.StringIO())
        clone_path = tmp_path / "repo"
        clone_path.mkdir()
        (clone_path / ".git").mkdir()
        # git pull will fail since it's not a real repo, but function returns True
        assert _clone_repo(console, clone_path) is True


class TestInstallDeps:
    """Tests for _install_deps error paths."""

    def test_uv_not_found(self, tmp_path):
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.shutil.which", return_value=None):
            assert _install_deps(console, tmp_path) is False


class TestHackathonCommand:
    """Integration test for the hackathon click command."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(hackathon, ["--help"])
        assert result.exit_code == 0
        assert "hackathon" in result.output.lower() or "fork" in result.output.lower()


class TestCheckoutProxyRef:
    """Tests for _checkout_proxy_ref()."""

    def test_checkout_branch(self, tmp_path):
        """Plain branch ref does git checkout."""
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _checkout_proxy_ref(console, tmp_path, "feature/foo")

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd == ["git", "checkout", "feature/foo"]

    def test_checkout_pr(self, tmp_path):
        """PR number fetches the PR ref and checks it out."""
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _checkout_proxy_ref(console, tmp_path, "pr-123", pr_number=123)

        assert result is True
        assert mock_run.call_count == 2
        fetch_cmd = mock_run.call_args_list[0].args[0]
        assert "+pull/123/head:pr-123" in " ".join(fetch_cmd)

    def test_checkout_failure(self, tmp_path):
        """Failed checkout returns False."""
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            result = _checkout_proxy_ref(console, tmp_path, "nonexistent")

        assert result is False


class TestHackathonProxyRef:
    """Tests for --proxy-ref on hackathon command."""

    def test_hackathon_help_shows_proxy_ref(self):
        runner = CliRunner()
        result = runner.invoke(hackathon, ["--help"])
        assert "--proxy-ref" in result.output

    def test_hackathon_invalid_pr_ref_errors(self):
        """Non-numeric PR ref like '#abc' should error, not crash."""
        runner = CliRunner()
        with (
            patch("luthien_cli.commands.hackathon.shutil.which", return_value="/usr/bin/gh"),
            patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(hackathon, ["--proxy-ref", "#abc", "-y"])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower() or "Invalid" in result.output
