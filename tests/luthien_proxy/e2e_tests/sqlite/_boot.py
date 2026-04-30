"""Shared boot helper for in-process SQLite-backed gateway fixtures.

Both `sqlite/conftest.py::sqlite_gateway_url` and
`sqlite/test_activity_stream.py::gateway_url` need to spin up the same
in-process SQLite gateway pointed at a mock Anthropic server. This helper
exists so they can't drift apart again — the divergence between the two
copies is what made #539 necessary after #538 fixed only one of them.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

import uvicorn

from luthien_proxy.main import create_app
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils.db import DatabasePool
from luthien_proxy.utils.migration_check import check_migrations


@dataclass(frozen=True)
class BootedSqliteGateway:
    url: str
    db_path: str


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@contextmanager
def boot_sqlite_gateway(
    *,
    api_key: str,
    admin_key: str,
    mock_anthropic_url: str,
    tmp_prefix: str,
    thread_name: str,
) -> Iterator[BootedSqliteGateway]:
    """Spin up an in-process SQLite gateway pointed at a mock Anthropic server.

    Yields a BootedSqliteGateway with url and db_path. Cleanup runs on both normal exit and any
    failure during setup — `ExitStack` registers each rollback as the matching
    resource is acquired, so a raise from `check_migrations()`, `create_app()`,
    or the gateway-startup wait still tears down everything that was set up.

    Why clear the settings cache: `get_settings()` is `@lru_cache`-wrapped, and
    pytest fixture ordering means session/module-scoped fixtures run *before*
    the function-scoped autouse cache clearer. Without an explicit clear here,
    a stale `Settings` instance from an earlier import path can poison
    `create_app()` so credential resolution fails. The teardown clear keeps
    the helper hermetic — the next caller resolves against the restored env.
    """
    port = free_port()

    with ExitStack() as stack:
        tmp_dir = tempfile.mkdtemp(prefix=tmp_prefix)
        stack.callback(shutil.rmtree, tmp_dir, ignore_errors=True)

        loop = asyncio.new_event_loop()
        stack.callback(loop.close)

        db_pool = DatabasePool(f"sqlite:///{os.path.join(tmp_dir, 'test.db')}")
        stack.callback(lambda: loop.run_until_complete(db_pool.close()))

        loop.run_until_complete(check_migrations(db_pool))

        old_env: dict[str, str | None] = {
            k: os.environ.get(k) for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ENABLE_REQUEST_LOGGING")
        }

        def restore_env() -> None:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        stack.callback(clear_settings_cache)
        stack.callback(restore_env)

        os.environ["ANTHROPIC_BASE_URL"] = mock_anthropic_url
        os.environ["ANTHROPIC_API_KEY"] = "mock-key"
        os.environ["ENABLE_REQUEST_LOGGING"] = "true"

        clear_settings_cache()
        app = create_app(
            api_key=api_key,
            admin_key=admin_key,
            db_pool=db_pool,
            redis_client=None,
            startup_policy_path="config/policy_config.yaml",
            policy_source="db-fallback-file",
        )

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name=thread_name)
        thread.start()

        def stop_server() -> None:
            server.should_exit = True
            thread.join(timeout=5)

        stack.callback(stop_server)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"SQLite gateway ({thread_name}) did not start")

        yield BootedSqliteGateway(url=f"http://127.0.0.1:{port}", db_path=os.path.join(tmp_dir, "test.db"))
