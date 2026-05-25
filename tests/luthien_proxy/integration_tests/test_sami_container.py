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
    """Container starts and gateway /health endpoint responds within 60s.

    Bypasses the interactive entrypoint (designed for TTY use) and runs
    the gateway directly so the container stays alive during the health poll.
    """
    port = _free_port()
    container_id = None
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "-p",
                f"{port}:8000",
                # Use a temp SQLite DB so no volume mount is needed
                "-e",
                "DATABASE_URL=sqlite:////tmp/test.db",
                # Override entrypoint to run gateway directly in foreground
                "--entrypoint",
                "/bin/sh",
                built_image,
                "-c",
                "cd /app && .venv/bin/python -m luthien_proxy.main",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"docker run failed: {result.stderr}"
        container_id = result.stdout.strip()

        # Wait up to 60s for gateway to be responsive
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            pytest.fail("Gateway did not respond within 60 seconds")
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
def test_container_gateway_health_from_inside(built_image: str) -> None:
    """Gateway /health responds from within the container (no external port mapping).

    Starts the gateway inside the container and verifies the in-container
    health check passes.  Replaces the former track_a_smoke.sh test which
    referenced a host-only dev script not present in the image.
    """
    health_cmd = (
        "cd /app && "
        ".venv/bin/python -m luthien_proxy.main --gateway-port 8000 & "
        "GW_PID=$! && "
        "for i in $(seq 1 60); do "
        "  curl -fsS http://localhost:8000/health > /dev/null 2>&1 && kill $GW_PID && exit 0; "
        "  sleep 1; "
        "done; "
        "kill $GW_PID 2>/dev/null || true; "
        "echo 'Gateway did not become healthy' >&2; exit 1"
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            "DATABASE_URL=sqlite:////tmp/test.db",
            "--entrypoint",
            "/bin/sh",
            built_image,
            "-c",
            health_cmd,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"In-container gateway health check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
