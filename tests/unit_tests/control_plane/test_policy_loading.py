from pathlib import Path

import pytest

from luthien_proxy.control_plane.policy_loader import load_policy_from_config
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.utils.project_config import ProjectConfig


def write_tmp_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(content)
    return path


def test_load_policy_from_valid_yaml(tmp_path: Path):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "luthien_proxy.policies.noop:NoOpPolicy"\n',
    )
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_falls_back_on_error(tmp_path: Path):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "does.not.exist:Missing"\n',
    )
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_requires_config_path(tmp_path: Path):
    config = ProjectConfig(env_map={})
    with pytest.raises(RuntimeError):
        load_policy_from_config(config, config_path=None)


def test_load_policy_passes_options(tmp_path: Path):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "luthien_proxy.policies.noop:NoOpPolicy"\npolicy_options:\n  unused: true\n',
    )
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
    yaml_path = write_tmp_yaml(tmp_path, 'policy: "builtins:object"\n')
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_without_policy_key(tmp_path: Path):
    yaml_path = write_tmp_yaml(tmp_path, "{}\n")
    config = ProjectConfig(env_map={})
    policy = load_policy_from_config(config, config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)
