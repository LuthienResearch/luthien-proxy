"""Tests for config module."""

import pytest
from luthien_cli.config import LuthienConfig, load_config, save_config


@pytest.fixture
def config_dir(tmp_path):
    """Use a temp dir for config instead of ~/.luthien/."""
    config_path = tmp_path / "config.toml"
    return tmp_path, config_path


def test_load_config_returns_defaults_when_no_file(config_dir):
    _, config_path = config_dir
    config = load_config(config_path)
    assert config.gateway_url == "http://localhost:8000"
    assert config.api_key is None
    assert config.admin_key is None
    assert config.repo_path is None


def test_save_and_load_roundtrip(config_dir):
    _, config_path = config_dir
    config = LuthienConfig(
        gateway_url="http://remote:9000",
        api_key="sk-test",
        admin_key="admin-test",
        repo_path="/home/user/luthien-proxy",
    )
    save_config(config, config_path)
    loaded = load_config(config_path)
    assert loaded.gateway_url == "http://remote:9000"
    assert loaded.api_key == "sk-test"
    assert loaded.admin_key == "admin-test"
    assert loaded.repo_path == "/home/user/luthien-proxy"


def test_save_creates_parent_directory(tmp_path):
    config_path = tmp_path / "subdir" / "config.toml"
    config = LuthienConfig()
    save_config(config, config_path)
    assert config_path.exists()


def test_load_config_ignores_unknown_keys(config_dir):
    _, config_path = config_dir
    config_path.write_text('[gateway]\nurl = "http://x"\nfoo = "bar"\n')
    config = load_config(config_path)
    assert config.gateway_url == "http://x"
