from pathlib import Path

import pytest

from luthien_control.control_plane.app import _load_policy_from_config
from luthien_control.policies.noop import NoOpPolicy


def write_tmp_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(content)
    return path


def test_load_policy_from_valid_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "luthien_control.policies.noop:NoOpPolicy"\n',
    )
    monkeypatch.setenv("LUTHIEN_POLICY_CONFIG", str(yaml_path))

    policy = _load_policy_from_config()
    assert isinstance(policy, NoOpPolicy)


def test_load_policy_falls_back_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    yaml_path = write_tmp_yaml(
        tmp_path,
        'policy: "does.not.exist:Missing"\n',
    )
    monkeypatch.setenv("LUTHIEN_POLICY_CONFIG", str(yaml_path))

    policy = _load_policy_from_config()
    assert isinstance(policy, NoOpPolicy)
