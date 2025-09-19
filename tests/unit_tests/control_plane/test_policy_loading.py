from pathlib import Path

from luthien_proxy.control_plane.app import _load_policy_from_config
from luthien_proxy.policies.noop import NoOpPolicy


def write_tmp_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(content)
    return path


def test_load_policy_from_valid_yaml(tmp_path: Path):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "luthien_proxy.policies.noop:NoOpPolicy"\n',
    )
    policy = _load_policy_from_config(config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_falls_back_on_error(tmp_path: Path):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "does.not.exist:Missing"\n',
    )
    policy = _load_policy_from_config(config_path=str(yaml_path))
    assert isinstance(policy, NoOpPolicy)
