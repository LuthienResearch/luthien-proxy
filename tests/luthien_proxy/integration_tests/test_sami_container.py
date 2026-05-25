# ABOUTME: Integration smoke tests for the Sami Docker container harness.
# ABOUTME: Verifies Dockerfile.sami builds correctly and the container has all required components.

"""Integration smoke tests for the Sami Docker container harness.

These tests verify that docker/Dockerfile.sami builds correctly and
the resulting container has all required components.

Most tests require Docker and are skipped when Docker is unavailable.
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_available() -> bool:
    """Return True if Docker daemon is reachable."""
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


@pytest.fixture(scope="session")
def built_image(docker_available: bool) -> str:
    """Build the luthien-sami image once per session; skip if Docker unavailable."""
    if not docker_available:
        pytest.skip("Docker daemon not reachable")
    image_tag = "luthien-sami:pytest"
    result = subprocess.run(
        ["docker", "build", "-f", "docker/Dockerfile.sami", "-t", image_tag, "."],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"docker build failed:\n{result.stderr}")
    return image_tag


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dockerfile_exists() -> None:
    """Dockerfile and entrypoint script exist and are well-formed."""
    dockerfile = Path("docker/Dockerfile.sami")
    entrypoint = Path("docker/sami-entrypoint.sh")
    assert dockerfile.exists(), "docker/Dockerfile.sami not found"
    assert entrypoint.exists(), "docker/sami-entrypoint.sh not found"
    content = dockerfile.read_text()
    assert content.startswith(("# syntax=", "FROM")), "Dockerfile must start with syntax comment or FROM"
    ep_content = entrypoint.read_text()
    assert ep_content.startswith("#!"), "Entrypoint must start with shebang"


@pytest.mark.slow
@pytest.mark.integration
def test_dockerfile_builds(built_image: str) -> None:
    """Docker image builds successfully from Dockerfile.sami."""
    result = subprocess.run(
        ["docker", "images", built_image, "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert built_image in result.stdout, f"Image {built_image} not found after build"


@pytest.mark.slow
@pytest.mark.integration
def test_container_boots_gateway(built_image: str) -> None:
    """Container starts and gateway /health endpoint responds within 30s."""
    port = _free_port()
    container_id = None
    try:
        result = subprocess.run(
            ["docker", "run", "-d", "-p", f"{port}:8000", built_image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"docker run failed: {result.stderr}"
        container_id = result.stdout.strip()

        # Wait up to 30s for gateway to be responsive
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            pytest.fail("Gateway did not respond within 30 seconds")
    finally:
        if container_id:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=30)
            subprocess.run(["docker", "rm", container_id], capture_output=True, timeout=30)


@pytest.mark.slow
@pytest.mark.integration
def test_container_has_opencode_and_plugin(built_image: str) -> None:
    """Container has opencode CLI and plugin built artifact."""
    # Check opencode CLI
    result = subprocess.run(
        ["docker", "run", "--rm", built_image, "which", "opencode"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "opencode CLI not found in container"
    assert result.stdout.strip(), "opencode path is empty"

    # Check plugin dist directory
    result = subprocess.run(
        ["docker", "run", "--rm", built_image, "ls", "/opt/opencode-luthien/dist/"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "Plugin dist directory not found"
    assert result.stdout.strip(), "Plugin dist directory is empty"


@pytest.mark.slow
@pytest.mark.integration
def test_track_a_smoke_passes_in_container(built_image: str) -> None:
    """track_a_smoke.sh exits 0 inside the container."""
    result = subprocess.run(
        ["docker", "run", "--rm", built_image, "/bin/bash", "-c", "scripts/track_a_smoke.sh"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"track_a_smoke.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
