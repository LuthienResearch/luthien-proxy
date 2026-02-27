# ABOUTME: Tests for V2 policy configuration loading from YAML
# ABOUTME: Covers successful loading and error handling (exceptions on invalid config)

"""Tests for v2.config module - policy loading from YAML."""

from __future__ import annotations

from pathlib import Path

import pytest

from luthien_proxy.config import _instantiate_policy, load_policy_from_yaml
from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core.base_policy import BasePolicy


class _StatelessConfigPolicy(BasePolicy):
    def __init__(self, flag: bool = False) -> None:
        self.flag = flag


class _StatefulBufferPolicy(BasePolicy):
    def __init__(self) -> None:
        self.buffer: dict[str, str] = {}


class TestLoadPolicyFromYaml:
    """Test suite for load_policy_from_yaml function."""

    def test_load_simple_policy(self, tmp_path: Path):
        """Test loading SimplePolicy from YAML config."""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.policies.simple_policy:SimplePolicy"
  config: {}
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, SimplePolicy)

    def test_load_all_caps_policy(self, tmp_path: Path):
        """Test loading AllCapsPolicy."""
        config_path = tmp_path / "test_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
  config: {}
"""
        )

        policy = load_policy_from_yaml(str(config_path))

        assert isinstance(policy, AllCapsPolicy)

    def test_missing_config_file_raises_exception(self, tmp_path: Path):
        """Test that missing config file raises FileNotFoundError."""
        nonexistent_path = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            load_policy_from_yaml(str(nonexistent_path))

    def test_invalid_yaml_raises_exception(self, tmp_path: Path):
        """Test that invalid YAML raises exception."""
        config_path = tmp_path / "invalid.yaml"
        config_path.write_text("this is not: valid: yaml: content")

        with pytest.raises(Exception):  # yaml.YAMLError or similar
            load_policy_from_yaml(str(config_path))

    def test_missing_policy_section_raises_exception(self, tmp_path: Path):
        """Test that YAML without 'policy' section raises ValueError."""
        config_path = tmp_path / "no_policy.yaml"
        config_path.write_text(
            """
other_config:
  some_value: 123
"""
        )

        with pytest.raises(ValueError):
            load_policy_from_yaml(str(config_path))

    def test_missing_class_raises_exception(self, tmp_path: Path):
        """Test that policy section without 'class' raises ValueError."""
        config_path = tmp_path / "no_class.yaml"
        config_path.write_text(
            """
policy:
  config:
    some_param: value
"""
        )

        with pytest.raises(ValueError):
            load_policy_from_yaml(str(config_path))

    def test_invalid_class_reference_raises_exception(self, tmp_path: Path):
        """Test that invalid class reference raises exception."""
        config_path = tmp_path / "invalid_class.yaml"
        config_path.write_text(
            """
policy:
  class: "nonexistent.module:NonexistentClass"
  config: {}
"""
        )

        with pytest.raises((ModuleNotFoundError, ImportError)):
            load_policy_from_yaml(str(config_path))

    def test_malformed_class_reference_raises_exception(self, tmp_path: Path):
        """Test that malformed class reference (no colon) raises exception."""
        config_path = tmp_path / "malformed.yaml"
        config_path.write_text(
            """
policy:
  class: "not_a_valid_reference"
  config: {}
"""
        )

        with pytest.raises(ValueError):
            load_policy_from_yaml(str(config_path))

    def test_non_policy_class_raises_exception(self, tmp_path: Path):
        """Test that class not inheriting from BasePolicy raises TypeError."""
        config_path = tmp_path / "non_policy.yaml"
        config_path.write_text(
            """
policy:
  class: "builtins:dict"
  config: {}
"""
        )

        # Should raise TypeError because dict doesn't inherit from BasePolicy
        with pytest.raises(TypeError, match="does not inherit from BasePolicy"):
            load_policy_from_yaml(str(config_path))

    def test_uses_policy_config_env_var(self, tmp_path: Path, monkeypatch):
        """Test that function respects POLICY_CONFIG environment variable."""
        config_path = tmp_path / "env_config.yaml"
        config_path.write_text(
            """
policy:
  class: "luthien_proxy.policies.simple_policy:SimplePolicy"
  config: {}
"""
        )

        monkeypatch.setenv("POLICY_CONFIG", str(config_path))

        # Call without explicit path - should use env var
        policy = load_policy_from_yaml()

        assert isinstance(policy, SimplePolicy)

    def test_default_path_when_no_env_var(self, tmp_path: Path, monkeypatch):
        """Test that default path is used when no env var or explicit path."""
        # Clear the env var
        monkeypatch.delenv("POLICY_CONFIG", raising=False)

        # Change to tmp_path so relative path config/policy_config.yaml won't exist
        monkeypatch.chdir(tmp_path)

        # Should try to load config/policy_config.yaml (which won't exist in test)
        # and raise FileNotFoundError
        with pytest.raises(FileNotFoundError):
            load_policy_from_yaml()


class TestPolicyInstantiationGuardrails:
    def test_instantiated_policy_is_frozen(self):
        """Configured policies should reject post-init instance mutation."""
        policy = _instantiate_policy(_StatelessConfigPolicy, {"flag": True})
        assert policy.flag is True

        with pytest.raises(AttributeError, match="frozen after configuration"):
            policy.runtime_state = "nope"

    def test_instantiation_rejects_mutable_instance_state(self):
        """Mutable instance containers are forbidden on policy objects."""
        with pytest.raises(TypeError, match="mutable container"):
            _instantiate_policy(_StatefulBufferPolicy, {})
