"""Tests for repo module -- managed proxy artifact directory."""

from unittest.mock import patch

import httpx
import pytest

from luthien_cli.repo import (
    GITHUB_RAW_BASE,
    GITHUB_SHA_URL,
    _download_files,
    _get_remote_sha,
    _strip_dev_only_lines,
    ensure_repo,
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
