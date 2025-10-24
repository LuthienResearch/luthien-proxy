# ABOUTME: Tests for V2 policy configuration loading from YAML
# ABOUTME: Covers successful loading, error handling, and fallback to NoOpPolicy

"""Tests for v2.config module - policy loading from YAML."""

from __future__ import annotations

from pathlib import Path

from luthien_proxy.v2.config import load_policy_from_yaml
from luthien_proxy.v2.policies.noop import NoOpPolicy
from luthien_proxy.v2.policies.uppercase_nth_word import UppercaseNthWordPolicy


class TestLoadPolicyFromYaml:
    """Test suite for load_policy_from_yaml function."""

    def test_load_noop_policy(self, tmp_path: Path):
        """Test loading NoOpPolicy from YAML config."""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.noop:NoOpPolicy"
  config: {}
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_load_uppercase_policy_with_config(self, tmp_path: Path):
        """Test loading UppercaseNthWordPolicy with config parameters."""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.uppercase_nth_word:UppercaseNthWordPolicy"
  config:
    n: 5
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, UppercaseNthWordPolicy)
        assert policy.n == 5

    def test_load_uppercase_policy_default_config(self, tmp_path: Path):
        """Test loading UppercaseNthWordPolicy without config (uses defaults)."""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.uppercase_nth_word:UppercaseNthWordPolicy"
  config:
    n: 3
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, UppercaseNthWordPolicy)
        assert policy.n == 3

    def test_missing_config_file_returns_noop(self, tmp_path: Path):
        """Test that missing config file returns NoOpPolicy with warning."""
        nonexistent_path = tmp_path / "nonexistent.yaml"

        policy = load_policy_from_yaml(str(nonexistent_path))

        assert isinstance(policy, NoOpPolicy)

    def test_invalid_yaml_returns_noop(self, tmp_path: Path):
        """Test that invalid YAML returns NoOpPolicy."""
        config_path = tmp_path / "invalid.yaml"
        config_path.write_text("this is not: valid: yaml: content")

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_missing_policy_section_returns_noop(self, tmp_path: Path):
        """Test that YAML without 'policy' section returns NoOpPolicy."""
        config_path = tmp_path / "no_policy.yaml"
        config_path.write_text(
            """
other_config:
  some_value: 123
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_missing_class_returns_noop(self, tmp_path: Path):
        """Test that policy section without 'class' returns NoOpPolicy."""
        config_path = tmp_path / "no_class.yaml"
        config_path.write_text(
            """
policy:
  config:
    some_param: value
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_invalid_class_reference_returns_noop(self, tmp_path: Path):
        """Test that invalid class reference returns NoOpPolicy."""
        config_path = tmp_path / "invalid_class.yaml"
        config_path.write_text(
            """
policy:
  class: "nonexistent.module:NonexistentClass"
  config: {}
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_malformed_class_reference_returns_noop(self, tmp_path: Path):
        """Test that malformed class reference (no colon) returns NoOpPolicy."""
        config_path = tmp_path / "malformed.yaml"
        config_path.write_text(
            """
policy:
  class: "not_a_valid_reference"
  config: {}
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_non_policy_class_returns_noop(self, tmp_path: Path):
        """Test that class not inheriting from LuthienPolicy returns NoOpPolicy."""
        config_path = tmp_path / "non_policy.yaml"
        config_path.write_text(
            """
policy:
  class: "builtins:dict"
  config: {}
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_invalid_config_parameters_returns_noop(self, tmp_path: Path):
        """Test that invalid config parameters return NoOpPolicy."""
        config_path = tmp_path / "bad_params.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.uppercase_nth_word:UppercaseNthWordPolicy"
  config:
    invalid_param: 123
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, NoOpPolicy)

    def test_uses_v2_policy_config_env_var(self, tmp_path: Path, monkeypatch):
        """Test that function respects V2_POLICY_CONFIG environment variable."""
        config_path = tmp_path / "env_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.noop:NoOpPolicy"
  config: {}
"""
        )

        monkeypatch.setenv("V2_POLICY_CONFIG", str(config_path))

        # Call without explicit path - should use env var
        policy = load_policy_from_yaml()

        assert isinstance(policy, NoOpPolicy)

    def test_explicit_path_overrides_env_var(self, tmp_path: Path, monkeypatch):
        """Test that explicit path parameter overrides environment variable."""
        # Create two different configs
        env_config = tmp_path / "env_config.yaml"
        env_config.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.noop:NoOpPolicy"
  config: {}
"""
        )

        explicit_config = tmp_path / "explicit_config.yaml"
        explicit_config.write_text(
            """
policy:
  class: "luthien_proxy.v2.policies.uppercase_nth_word:UppercaseNthWordPolicy"
  config:
    n: 7
"""
        )

        monkeypatch.setenv("V2_POLICY_CONFIG", str(env_config))

        # Call with explicit path - should use explicit path, not env var
        policy = load_policy_from_yaml(str(explicit_config))

        assert isinstance(policy, UppercaseNthWordPolicy)
        assert policy.n == 7

    def test_default_path_when_no_env_var(self, tmp_path: Path, monkeypatch):
        """Test that default path is used when no env var or explicit path."""
        # Clear the env var
        monkeypatch.delenv("V2_POLICY_CONFIG", raising=False)

        # Change to tmp_path so relative path config/v2_config.yaml won't exist
        monkeypatch.chdir(tmp_path)

        # Should try to load config/v2_config.yaml (which won't exist in test)
        # and return NoOpPolicy
        policy = load_policy_from_yaml()

        assert isinstance(policy, NoOpPolicy)
