"""Tests for repo module -- managed proxy artifact directory."""

from unittest.mock import patch

import httpx
import pytest
import yaml

from luthien_cli.repo import (
    _DEFAULT_POLICY_CONFIG_YAML,
    GITHUB_RAW_BASE,
    GITHUB_SHA_URL,
    _download_files,
    _get_remote_sha,
    _remove_build_blocks,
    _strip_dev_only_lines,
    _write_default_policy_config,
    ensure_gateway_venv,
    ensure_repo,
    resolve_proxy_ref,
)


def test_get_remote_sha(httpx_mock):
    httpx_mock.add_response(
        url=GITHUB_SHA_URL,
        text="abc123def456",
    )
    assert _get_remote_sha() == "abc123def456"


def test_get_remote_sha_strips_whitespace(httpx_mock):
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="  abc123\n")
    assert _get_remote_sha() == "abc123"


def test_get_remote_sha_network_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("offline"))
    with pytest.raises(httpx.ConnectError):
        _get_remote_sha()


SAMPLE_COMPOSE = """\
services:
  migrations:
    image: ghcr.io/luthienresearch/luthien-proxy/migrations:latest
    build:
      context: .
      dockerfile: docker/Dockerfile.migrations
    environment:
      PGHOST: db
  gateway:
    image: ghcr.io/luthienresearch/luthien-proxy/gateway:latest
    build:
      context: .
      dockerfile: docker/Dockerfile.gateway
    env_file: .env
    volumes:
      - ./src:/app/src:ro
      - ./config:/app/config:ro
    ports:
      - "8000:8000"
  sandbox:
    image: ghcr.io/luthienresearch/luthien-proxy/sandbox:latest
    build:
      context: .
      dockerfile: docker/sandbox/Dockerfile
    profiles: ["overseer"]
"""


def test_strip_dev_only_lines():
    result = _strip_dev_only_lines(SAMPLE_COMPOSE)
    assert "./src:/app/src:ro" not in result
    assert "./config:/app/config:ro" in result
    assert "build:" not in result
    assert "context: ." not in result
    assert "dockerfile:" not in result
    # Structural elements preserved
    assert "image: ghcr.io/luthienresearch/luthien-proxy/gateway:latest" in result
    assert "env_file: .env" in result
    assert 'profiles: ["overseer"]' in result


def test_strip_dev_only_lines_no_match():
    """Content without dev-only lines passes through unchanged."""
    content = "services:\n  db:\n    image: postgres:16\n"
    assert _strip_dev_only_lines(content) == content


def test_remove_build_blocks_empty_block():
    """A build: key with no nested lines is removed."""
    content = "services:\n  gw:\n    image: x\n    build:\n    env_file: .env\n"
    result = _remove_build_blocks(content)
    assert "build:" not in result
    assert "env_file: .env" in result


def test_remove_build_blocks_different_indent():
    """Build blocks at varying indent levels are all removed."""
    content = (
        "services:\n"
        "  a:\n"
        "    build:\n"
        "      context: .\n"
        "  b:\n"
        "    image: x\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n"
        "    ports:\n"
        "      - '80:80'\n"
    )
    result = _remove_build_blocks(content)
    assert "build:" not in result
    assert "context:" not in result
    assert "dockerfile:" not in result
    assert "image: x" in result
    assert "ports:" in result


def test_remove_build_blocks_preserves_non_build_nesting():
    """Nested content under non-build keys is preserved."""
    content = "services:\n  gw:\n    environment:\n      FOO: bar\n      BAZ: qux\n"
    assert _remove_build_blocks(content) == content


def test_download_files(tmp_path, httpx_mock):
    compose_content = SAMPLE_COMPOSE
    env_content = "PROXY_API_KEY=placeholder\n"

    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}docker-compose.yaml",
        text=compose_content,
    )
    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}.env.example",
        text=env_content,
    )
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha123")

    _download_files(tmp_path)

    compose = (tmp_path / "docker-compose.yaml").read_text()
    assert "./src:/app/src" not in compose
    assert "build:" not in compose
    assert "./config:/app/config:ro" in compose

    assert (tmp_path / ".env.example").read_text() == env_content
    assert (tmp_path / ".version").read_text() == "sha123"
    assert (tmp_path / "config").is_dir()


def test_download_files_network_error(tmp_path, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("offline"))
    with pytest.raises(SystemExit):
        _download_files(tmp_path)


def test_ensure_repo_fresh_install(tmp_path, httpx_mock):
    managed = tmp_path / "luthien-proxy"
    compose_content = "services:\n  gateway:\n    image: ghcr.io/x:latest\n"

    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}docker-compose.yaml",
        text=compose_content,
    )
    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}.env.example",
        text="KEY=val\n",
    )
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha-fresh")

    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        result = ensure_repo()

    assert result == str(managed)
    assert (managed / ".version").read_text() == "sha-fresh"


def test_ensure_repo_up_to_date(tmp_path, httpx_mock):
    managed = tmp_path / "luthien-proxy"
    managed.mkdir()
    (managed / ".version").write_text("sha-current")
    (managed / "docker-compose.yaml").write_text("existing")

    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha-current")

    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        result = ensure_repo()

    assert result == str(managed)


def test_ensure_repo_update_available_accepted(tmp_path, httpx_mock):
    managed = tmp_path / "luthien-proxy"
    managed.mkdir()
    (managed / ".version").write_text("sha-old")
    (managed / "docker-compose.yaml").write_text("old")

    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha-new")
    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}docker-compose.yaml",
        text="services:\n  gw:\n    image: x\n",
    )
    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}.env.example",
        text="K=V\n",
    )
    # Second SHA call during _download_files
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha-new")

    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        with patch("click.confirm", return_value=True):
            ensure_repo()

    assert (managed / ".version").read_text() == "sha-new"


def test_ensure_repo_update_declined(tmp_path, httpx_mock):
    managed = tmp_path / "luthien-proxy"
    managed.mkdir()
    (managed / ".version").write_text("sha-old")
    (managed / "docker-compose.yaml").write_text("old")

    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha-new")

    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        with patch("click.confirm", return_value=False):
            result = ensure_repo()

    assert result == str(managed)
    assert (managed / ".version").read_text() == "sha-old"


def test_ensure_repo_sha_check_fails_existing_install(tmp_path, httpx_mock):
    """Network error during version check is non-fatal if files exist."""
    managed = tmp_path / "luthien-proxy"
    managed.mkdir()
    (managed / ".version").write_text("sha-old")
    (managed / "docker-compose.yaml").write_text("existing")

    httpx_mock.add_exception(httpx.ConnectError("offline"))

    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        result = ensure_repo()

    assert result == str(managed)


def test_ensure_repo_fresh_install_network_error(tmp_path, httpx_mock):
    """Network error on fresh install should fail with SystemExit."""
    managed = tmp_path / "luthien-proxy"
    httpx_mock.add_exception(httpx.ConnectError("offline"))
    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        with pytest.raises(SystemExit):
            ensure_repo()


def test_resolve_proxy_ref_plain_branch():
    """Plain ref passes through unchanged."""
    assert resolve_proxy_ref("feature/foo") == "feature/foo"


def test_resolve_proxy_ref_commit_sha():
    """Commit SHA passes through unchanged."""
    assert resolve_proxy_ref("abc1234def") == "abc1234def"


def test_resolve_proxy_ref_pr_number(httpx_mock):
    """PR ref #123 resolves to the PR's head branch."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/123",
        json={"head": {"ref": "feature/cool-thing"}},
    )
    assert resolve_proxy_ref("#123") == "feature/cool-thing"


def test_resolve_proxy_ref_pr_not_found(httpx_mock):
    """PR ref for non-existent PR raises SystemExit."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/999",
        status_code=404,
    )
    with pytest.raises(SystemExit):
        resolve_proxy_ref("#999")


def test_resolve_proxy_ref_pr_network_error(httpx_mock):
    """Network error resolving PR raises SystemExit."""
    httpx_mock.add_exception(
        httpx.ConnectError("offline"),
        url="https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/42",
    )
    with pytest.raises(SystemExit):
        resolve_proxy_ref("#42")


def test_ensure_gateway_venv_with_proxy_ref(tmp_path):
    """proxy_ref appends @ref to the git install URL."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv") as mock_uv,
    ):
        ensure_gateway_venv(proxy_ref="feature/cool")

    install_call = [c for c in mock_uv.call_args_list if c.args[0] == "pip"][0]
    install_args = list(install_call.args)
    github_url = [a for a in install_args if "github.com" in a][0]
    assert github_url.endswith("@feature/cool")


def test_ensure_gateway_venv_without_proxy_ref(tmp_path):
    """No proxy_ref uses bare git URL (no @suffix) and --upgrade (not --reinstall)."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"
    # Pre-create venv so needs_install=False
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").touch()

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv") as mock_uv,
    ):
        ensure_gateway_venv()

    install_call = [c for c in mock_uv.call_args_list if c.args[0] == "pip"][0]
    install_args = list(install_call.args)
    github_url = [a for a in install_args if "github.com" in a][0]
    assert github_url == "git+https://github.com/LuthienResearch/luthien-proxy.git"
    assert "--upgrade" in install_args
    assert "--reinstall-package" not in install_args


def test_ensure_gateway_venv_force_reinstall(tmp_path):
    """force_reinstall=True uses --reinstall-package even without proxy_ref."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").touch()

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv") as mock_uv,
    ):
        ensure_gateway_venv(force_reinstall=True)

    install_call = [c for c in mock_uv.call_args_list if c.args[0] == "pip"][0]
    install_args = list(install_call.args)
    assert "--reinstall-package" in install_args
    assert "luthien-proxy" in install_args


def test_ensure_gateway_venv_proxy_ref_uses_reinstall(tmp_path):
    """proxy_ref triggers --reinstall-package to force fresh fetch."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").touch()

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv") as mock_uv,
    ):
        ensure_gateway_venv(proxy_ref="feature/x")

    install_call = [c for c in mock_uv.call_args_list if c.args[0] == "pip"][0]
    install_args = list(install_call.args)
    assert "--reinstall-package" in install_args


def test_ensure_repo_force_update_redownloads(tmp_path, httpx_mock):
    """force_update=True re-downloads even when SHA matches."""
    managed = tmp_path / "luthien-proxy"
    managed.mkdir()
    (managed / ".version").write_text("sha-current")
    (managed / "docker-compose.yaml").write_text("existing")

    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}docker-compose.yaml",
        text="services:\n",
    )
    httpx_mock.add_response(
        url=f"{GITHUB_RAW_BASE}.env.example",
        text="K=V\n",
    )
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha-current")

    with patch("luthien_cli.repo.MANAGED_REPO_DIR", managed):
        result = ensure_repo(force_update=True)

    assert result == str(managed)
    assert (managed / "docker-compose.yaml").read_text() == "services:\n"


def test_ensure_gateway_venv_with_ref_shows_message(tmp_path, capsys):
    """Using a ref prints a message about the ref being used."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv"),
    ):
        ensure_gateway_venv(proxy_ref="abc123")

    captured = capsys.readouterr()
    assert "abc123" in captured.err


def test_ensure_gateway_venv_creates_default_policy_config(tmp_path):
    """ensure_gateway_venv creates default policy_config.yaml if missing."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv"),
    ):
        ensure_gateway_venv()

    policy_config = repo_dir / "config" / "policy_config.yaml"
    assert policy_config.exists()
    content = policy_config.read_text()
    assert "NoOpPolicy" in content
    assert "policy:" in content


def test_ensure_gateway_venv_preserves_existing_policy_config(tmp_path):
    """ensure_gateway_venv preserves existing policy_config.yaml."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"
    (repo_dir / "config").mkdir(parents=True)
    custom_content = "policy:\n  class: custom:MyPolicy\n  config: {}\n"
    (repo_dir / "config" / "policy_config.yaml").write_text(custom_content)

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv"),
    ):
        ensure_gateway_venv()

    content = (repo_dir / "config" / "policy_config.yaml").read_text()
    assert content == custom_content


def test_download_files_creates_default_policy_config(tmp_path, httpx_mock):
    """_download_files creates default policy_config.yaml if missing."""
    httpx_mock.add_response(url=f"{GITHUB_RAW_BASE}docker-compose.yaml", text="services:\n  gw:\n    image: x\n")
    httpx_mock.add_response(url=f"{GITHUB_RAW_BASE}.env.example", text="K=V\n")
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha123")

    _download_files(tmp_path)

    policy_config = tmp_path / "config" / "policy_config.yaml"
    assert policy_config.exists()
    content = policy_config.read_text()
    assert "NoOpPolicy" in content
    assert "policy:" in content


def test_download_files_preserves_existing_policy_config(tmp_path, httpx_mock):
    """_download_files preserves existing policy_config.yaml."""
    (tmp_path / "config").mkdir()
    custom_content = "policy:\n  class: custom:MyPolicy\n  config: {}\n"
    (tmp_path / "config" / "policy_config.yaml").write_text(custom_content)

    httpx_mock.add_response(url=f"{GITHUB_RAW_BASE}docker-compose.yaml", text="services:\n  gw:\n    image: x\n")
    httpx_mock.add_response(url=f"{GITHUB_RAW_BASE}.env.example", text="K=V\n")
    httpx_mock.add_response(url=GITHUB_SHA_URL, text="sha123")

    _download_files(tmp_path)

    content = (tmp_path / "config" / "policy_config.yaml").read_text()
    assert content == custom_content


def test_default_policy_config_yaml_is_valid(tmp_path):
    """The seeded default policy_config.yaml is valid YAML with expected structure."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv"),
    ):
        ensure_gateway_venv()

    content = (repo_dir / "config" / "policy_config.yaml").read_text()
    parsed = yaml.safe_load(content)
    assert parsed is not None
    assert "policy" in parsed
    assert "class" in parsed["policy"]
    assert "NoOpPolicy" in parsed["policy"]["class"]


def test_write_default_policy_config_creates_file(tmp_path):
    """_write_default_policy_config writes the constant to disk when file is absent."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    _write_default_policy_config(config_dir)

    written = (config_dir / "policy_config.yaml").read_text()
    assert written == _DEFAULT_POLICY_CONFIG_YAML


def test_write_default_policy_config_skips_existing(tmp_path):
    """_write_default_policy_config does not overwrite an existing file."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    existing = "policy:\n  class: custom:MyPolicy\n  config: {}\n"
    (config_dir / "policy_config.yaml").write_text(existing)

    _write_default_policy_config(config_dir)

    assert (config_dir / "policy_config.yaml").read_text() == existing


def test_default_policy_config_matches_canonical():
    """_DEFAULT_POLICY_CONFIG_YAML active policy class matches config/policy_config.yaml."""
    from pathlib import Path

    import yaml

    repo_root = Path(__file__).parents[2]
    canonical = yaml.safe_load((repo_root / "config" / "policy_config.yaml").read_text())
    seeded = yaml.safe_load(_DEFAULT_POLICY_CONFIG_YAML)

    assert canonical["policy"]["class"] == seeded["policy"]["class"], (
        f"_DEFAULT_POLICY_CONFIG_YAML policy class '{seeded['policy']['class']}' "
        f"differs from canonical '{canonical['policy']['class']}'. "
        "Update _DEFAULT_POLICY_CONFIG_YAML in repo.py to match."
    )
    repo_root = __import__("pathlib").Path(__file__).parents[2]
    canonical_file = repo_root / "config" / "policy_config.yaml"

    canonical = yaml.safe_load(canonical_file.read_text())
    seeded = yaml.safe_load(_DEFAULT_POLICY_CONFIG_YAML)

    assert canonical["policy"]["class"] == seeded["policy"]["class"], (
        f"_DEFAULT_POLICY_CONFIG_YAML policy class '{seeded['policy']['class']}' "
        f"differs from canonical config/policy_config.yaml '{canonical['policy']['class']}'. "
        "Update _DEFAULT_POLICY_CONFIG_YAML in repo.py to match."
    )
