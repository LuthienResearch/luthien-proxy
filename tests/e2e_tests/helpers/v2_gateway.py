"""ABOUTME: Helper for managing V2 gateway lifecycle in e2e tests.
ABOUTME: Provides self-contained V2 gateway that doesn't interfere with dev environment.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
from contextlib import contextmanager
from typing import Optional

import httpx
import uvicorn

from luthien_proxy.v2.main import create_app
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.noop import NoOpPolicy


def _run_v2_gateway(port: int, api_key: str) -> None:
    """Run V2 gateway in a subprocess.

    This function is the target for multiprocessing.Process.
    It configures the environment and starts uvicorn.
    """
    # Get config from environment
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable required for e2e tests")

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    # Create policy
    policy: LuthienPolicy = NoOpPolicy()
    # Create app with factory
    app = create_app(
        api_key=api_key,
        database_url=database_url,
        redis_url=redis_url,
        policy=policy,
    )

    # Run uvicorn server
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",  # Quiet logs during tests
        access_log=False,
    )


class V2GatewayManager:
    """Manager for V2 gateway test instances.

    Starts a V2 gateway on a dedicated test port, waits for it to be ready,
    and ensures cleanup when done. Does not interfere with dev environment.
    """

    def __init__(
        self,
        port: int = 8888,
        api_key: str = "sk-test-v2-gateway",
        startup_timeout: float = 10.0,
        verbose: bool = False,
    ):
        self.port = port
        self.api_key = api_key
        self.startup_timeout = startup_timeout
        self.verbose = verbose
        self.base_url = f"http://127.0.0.1:{port}"
        self._process: Optional[multiprocessing.Process] = None

    def start(self) -> None:
        """Start the V2 gateway subprocess."""
        if self._process is not None:
            raise RuntimeError("V2 gateway already started")

        if self.verbose:
            print(f"[v2-gateway] Starting V2 gateway on port {self.port}")

        # Start gateway in subprocess
        self._process = multiprocessing.Process(
            target=_run_v2_gateway,
            args=(self.port, self.api_key),
            daemon=True,
        )
        self._process.start()

        # Wait for gateway to be ready
        deadline = time.monotonic() + self.startup_timeout
        last_error: Optional[Exception] = None

        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=1.0)
                response.raise_for_status()
                if self.verbose:
                    print("[v2-gateway] Gateway is ready")
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)

        # Cleanup on failure
        self.stop()
        raise RuntimeError(
            f"V2 gateway failed to start within {self.startup_timeout}s" + (f": {last_error}" if last_error else "")
        )

    def stop(self) -> None:
        """Stop the V2 gateway subprocess."""
        if self._process is None:
            return

        if self.verbose:
            print("[v2-gateway] Stopping V2 gateway")

        # Terminate process
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5.0)

        # Force kill if still alive
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=1.0)

        self._process = None

    @contextmanager
    def running(self):
        """Context manager for V2 gateway lifecycle."""
        try:
            self.start()
            yield self
        finally:
            self.stop()


async def wait_for_v2_gateway(
    base_url: str,
    timeout: float = 10.0,
    verbose: bool = False,
) -> None:
    """Wait for V2 gateway to become healthy.

    Args:
        base_url: Base URL of the gateway (e.g., "http://localhost:8888")
        timeout: Maximum time to wait in seconds
        verbose: Whether to print progress messages
    """
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None

    async with httpx.AsyncClient(timeout=1.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{base_url}/health")
                response.raise_for_status()
                if verbose:
                    print(f"[v2-gateway] Gateway at {base_url} is healthy")
                return
            except Exception as exc:
                last_error = exc
                if verbose:
                    print(f"[v2-gateway] Waiting for gateway: {exc}")
                await asyncio.sleep(0.2)

    raise RuntimeError(
        f"V2 gateway at {base_url} failed to become healthy within {timeout}s"
        + (f": {last_error}" if last_error else "")
    )


__all__ = [
    "V2GatewayManager",
    "wait_for_v2_gateway",
]
