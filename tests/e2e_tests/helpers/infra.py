"""Shared infrastructure helpers for e2e tests."""

from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlparse

import httpx
import pytest


@dataclass(frozen=True)
class E2ESettings:
    project_root: pathlib.Path
    proxy_url: str
    control_plane_url: str
    master_key: str
    model_name: str
    scenario: str
    target_policy_config: Optional[str]
    request_timeout: float
    trace_retries: int
    trace_retry_delay: float
    control_plane_restart_timeout: float
    verbose: bool
    dummy_provider_url: str


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _control_plane_public_url() -> str:
    """Return a control-plane URL reachable from the host running the tests."""

    default_url = "http://localhost:8081"
    explicit_public = os.getenv("CONTROL_PLANE_PUBLIC_URL")
    if explicit_public:
        return explicit_public
    candidate = os.getenv("CONTROL_PLANE_URL")
    if not candidate:
        return default_url
    parsed = urlparse(candidate)
    host = parsed.hostname or ""
    if host.lower() == "control-plane":
        return default_url
    return candidate


def load_e2e_settings() -> E2ESettings:
    project_root = pathlib.Path(__file__).resolve().parents[3]
    return E2ESettings(
        project_root=project_root,
        proxy_url=os.getenv("LITELLM_PROXY_URL", "http://localhost:4000"),
        control_plane_url=_control_plane_public_url(),
        master_key=os.getenv("LITELLM_MASTER_KEY", "sk-luthien-dev-key"),
        model_name=os.getenv("SQL_POLICY_E2E_MODEL", "dummy-agent"),
        scenario=os.getenv("SQL_POLICY_E2E_SCENARIO", "harmful_drop"),
        target_policy_config=os.getenv("SQL_POLICY_CONFIG_PATH", "/app/config/luthien_demo_config.yaml"),
        request_timeout=float(os.getenv("SQL_POLICY_E2E_TIMEOUT", "15")),
        trace_retries=int(os.getenv("SQL_POLICY_E2E_TRACE_RETRIES", "10")),
        trace_retry_delay=float(os.getenv("SQL_POLICY_E2E_TRACE_DELAY", "0.3")),
        control_plane_restart_timeout=float(os.getenv("SQL_POLICY_CONTROL_PLANE_TIMEOUT", "60")),
        verbose=_env_flag("SQL_POLICY_E2E_VERBOSE"),
        dummy_provider_url=os.getenv("DUMMY_PROVIDER_URL", "http://localhost:4015/health"),
    )


def _read_env_default(project_root: pathlib.Path, key: str) -> Optional[str]:
    value = os.environ.get(key)
    if value:
        return value
    env_path = project_root / ".env"
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw_value = line.partition("=")
        if name.strip() == key:
            return raw_value.strip().strip('"').strip("'")
    return None


def _run_docker_compose(settings: E2ESettings, args: list[str], env: Mapping[str, str]) -> None:
    command = ["docker", "compose", *args]
    if settings.verbose:
        print(f"[e2e] running: {' '.join(command)}")
    try:
        subprocess.run(
            command,
            check=True,
            cwd=settings.project_root,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        pytest.skip(f"docker compose not available: {exc}")
    except subprocess.CalledProcessError as exc:  # pragma: no cover - expose compose output on failure
        output = exc.stdout.decode() if exc.stdout else ""
        raise AssertionError(f"docker compose command failed: {command}\n{output}") from exc


async def wait_for_control_plane_ready(settings: E2ESettings, timeout: float | None = None) -> None:
    if timeout is None:
        timeout = settings.control_plane_restart_timeout
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{settings.control_plane_url}/health")
                if settings.verbose:
                    print(f"[e2e] control-plane health attempt: status={response.status_code}")
                response.raise_for_status()
                if settings.verbose:
                    print("[e2e] control-plane reported healthy")
                return
            except Exception as exc:
                last_error = exc
                if settings.verbose:
                    print(f"[e2e] health check failed: {exc}")
                await asyncio.sleep(0.5)
        raise RuntimeError(
            "Control plane failed to become healthy after restart" + (f": {last_error}" if last_error else "")
        )


async def wait_for_dummy_provider_ready(settings: E2ESettings, timeout: float | None = None) -> None:
    if timeout is None:
        timeout = settings.control_plane_restart_timeout
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(settings.dummy_provider_url)
                if settings.verbose:
                    print(f"[e2e] dummy-provider health attempt: status={response.status_code}")
                response.raise_for_status()
                if settings.verbose:
                    print("[e2e] dummy-provider reported healthy")
                return
            except Exception as exc:
                last_error = exc
                if settings.verbose:
                    print(f"[e2e] dummy-provider health check failed: {exc}")
                await asyncio.sleep(0.5)
        raise RuntimeError("Dummy provider failed to become healthy" + (f": {last_error}" if last_error else ""))


async def ensure_services_available(settings: E2ESettings) -> None:
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        try:
            if settings.verbose:
                print("[e2e] checking proxy health endpoint")
            proxy_health = await client.get(f"{settings.proxy_url}/test")
            proxy_health.raise_for_status()
            if settings.verbose:
                print("[e2e] proxy health OK")
        except Exception as exc:
            pytest.skip(f"LiteLLM proxy not reachable at {settings.proxy_url}/test: {exc}")
        try:
            if settings.verbose:
                print("[e2e] checking control-plane health endpoint")
            control_health = await client.get(f"{settings.control_plane_url}/health")
            control_health.raise_for_status()
            if settings.verbose:
                print("[e2e] control-plane health OK")
        except Exception as exc:
            pytest.skip(f"Control plane not reachable at {settings.control_plane_url}/health: {exc}")


async def fetch_debug_entries(
    settings: E2ESettings,
    debug_type: str,
    *,
    limit: int = 200,
) -> list[dict[str, object]]:
    """Return recent debug entries for a debug type."""
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.get(
            f"{settings.control_plane_url}/api/debug/{debug_type}",
            params={"limit": limit},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []


async def wait_for_debug_entry(
    settings: E2ESettings,
    debug_type: str,
    predicate,
) -> dict[str, object]:
    """Poll debug logs for an entry that satisfies predicate."""
    for _ in range(settings.trace_retries):
        entries = await fetch_debug_entries(settings, debug_type, limit=200)
        for entry in entries:
            if isinstance(entry, Mapping) and predicate(entry):
                return dict(entry)
        await asyncio.sleep(settings.trace_retry_delay)
    raise AssertionError(f"Debug entry for type {debug_type} not found after {settings.trace_retries} retries")


@contextmanager
def dummy_provider_running(settings: E2ESettings):
    env = os.environ.copy()
    _run_docker_compose(
        settings,
        [
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "dummy-provider",
        ],
        env,
    )
    asyncio.run(wait_for_dummy_provider_ready(settings))
    try:
        yield
    finally:
        try:
            _run_docker_compose(settings, ["stop", "dummy-provider"], env)
        except AssertionError:
            pass
        try:
            _run_docker_compose(settings, ["rm", "-f", "dummy-provider"], env)
        except AssertionError:
            pass


class ControlPlaneManager:
    """Utility for restarting the control-plane container with different policies."""

    def __init__(self, settings: E2ESettings) -> None:
        self._settings = settings
        self._base_policy = _read_env_default(settings.project_root, "LUTHIEN_POLICY_CONFIG")
        self._current_policy = self._base_policy

    def _container_policy_path(self, policy_config: Optional[str]) -> Optional[str]:
        """Translate host paths into the container mount layout under /app."""

        if policy_config is None:
            return None

        if policy_config.startswith("/app/"):
            return policy_config

        path = pathlib.Path(policy_config)
        if not path.is_absolute():
            return f"/app/{path.as_posix().lstrip('./')}"

        try:
            relative = path.relative_to(self._settings.project_root)
        except ValueError:
            return policy_config

        return f"/app/{relative.as_posix()}"

    def _restart(self, policy_config: Optional[str]) -> None:
        env = os.environ.copy()
        container_path = self._container_policy_path(policy_config)
        if policy_config is None:
            env.pop("LUTHIEN_POLICY_CONFIG", None)
        else:
            env["LUTHIEN_POLICY_CONFIG"] = container_path
        if self._settings.verbose:
            print(f"[e2e] restarting control-plane with policy={container_path or self._base_policy}")
        _run_docker_compose(
            self._settings,
            [
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                "--force-recreate",
                "control-plane",
            ],
            env,
        )
        asyncio.run(wait_for_control_plane_ready(self._settings))
        self._current_policy = policy_config

    @contextmanager
    def apply_policy(self, policy_config: Optional[str]):
        previous = self._current_policy
        if previous == policy_config:
            yield
            return
        self._restart(policy_config)
        try:
            yield
        finally:
            self._restart(previous)


__all__ = [
    "E2ESettings",
    "ControlPlaneManager",
    "dummy_provider_running",
    "ensure_services_available",
    "fetch_debug_entries",
    "wait_for_debug_entry",
    "load_e2e_settings",
]
