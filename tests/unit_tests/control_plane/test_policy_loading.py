from pathlib import Path

import pytest

from luthien_proxy.control_plane.policy_loader import load_policy_from_config
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.policies.streaming_separator import StreamingSeparatorPolicy
from luthien_proxy.utils.project_config import ProjectConfig


def write_tmp_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(content)
    return path


def test_load_policy_from_valid_yaml(tmp_path: Path):
    yaml_content = """policy:
  class: "luthien_proxy.policies.noop:NoOpPolicy"
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_falls_back_on_error(tmp_path: Path):
    yaml_content = """policy:
  class: "does.not.exist:Missing"
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_requires_config_path(tmp_path: Path):
    config = ProjectConfig(env_map={})
    with pytest.raises(RuntimeError):
        load_policy_from_config(config, config_path=None)


def test_load_policy_passes_options(tmp_path: Path):
    yaml_content = """policy:
  class: "luthien_proxy.policies.noop:NoOpPolicy"
  config:
    unused: true
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_handles_missing_file(tmp_path: Path):
    config = ProjectConfig(env_map={})
    missing = tmp_path / "missing.yaml"
    policy = load_policy_from_config(config, config_path=str(missing))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_handles_yaml_error(tmp_path: Path):
    yaml_path = write_tmp_yaml(tmp_path, "policy: [invalid\n")
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_rejects_non_subclass(tmp_path: Path):
    yaml_content = """policy:
  class: "builtins:object"
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_without_policy_key(tmp_path: Path):
    yaml_path = write_tmp_yaml(tmp_path, "{}\n")
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_nested_format_with_config(tmp_path: Path):
    """Test new nested config format with policy.class and policy.config."""
    yaml_content = """policy:
  class: "luthien_proxy.policies.streaming_separator:StreamingSeparatorPolicy"
  config:
    every_n: 3
    separator_str: " >>> "
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, StreamingSeparatorPolicy)
    assert policy.every_n == 3
    assert policy.separator_str == " >>> "


def test_load_policy_nested_format_empty_config(tmp_path: Path):
    """Test new nested format with empty config dict."""
    yaml_content = """policy:
  class: "luthien_proxy.policies.noop:NoOpPolicy"
  config: {}
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_nested_format_no_config(tmp_path: Path):
    """Test new nested format without config field."""
    yaml_content = """policy:
  class: "luthien_proxy.policies.noop:NoOpPolicy"
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_old_format_no_longer_works(tmp_path: Path):
    """Test that old flat format is no longer supported."""
    yaml_content = """policy: "luthien_proxy.policies.noop:NoOpPolicy"
policy_options:
  some_option: value
"""
    yaml_path = write_tmp_yaml(tmp_path, yaml_content)
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    # Old format should fall back to NoOpPolicy since policy is not a dict
    assert isinstance(policy, NoOpPolicy)
