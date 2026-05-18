#!/usr/bin/env python3
"""Start an in-process gateway + mock Anthropic server for e2e testing.

Prints JSON with gateway_url, api_key, admin_api_key to stdout,
then blocks until SIGTERM/SIGINT. Used by run_e2e.sh for the mock tier.
"""

import asyncio
import json
import os
import shutil
import signal
import socket
import sqlite3
import sys
import tempfile
import threading
import time

# Set env vars BEFORE importing luthien_proxy — init_sentry() runs at import
# time and caches get_settings() via lru_cache, so env must be set first.
os.environ.setdefault("ENABLE_REQUEST_LOGGING", "true")

import uvicorn  # noqa: E402

from luthien_proxy.main import create_app  # noqa: E402
from luthien_proxy.utils.db import DatabasePool  # noqa: E402
from luthien_proxy.utils.migration_check import check_migrations  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def main():
    api_key = "test-mock-e2e-key"
    admin_api_key = "test-mock-e2e-admin-key"

    # Port handoff: we pick a port here and configure the gateway's
    # ANTHROPIC_BASE_URL to point at it. The actual mock server is started
    # later by pytest's mock_anthropic fixture, which reads MOCK_ANTHROPIC_PORT
    # from the environment to bind to the same port.
    mock_port = int(os.getenv("MOCK_ANTHROPIC_PORT", "0")) or _free_port()

    # Create SQLite gateway
    gateway_port = _free_port()
    tmp_dir = tempfile.mkdtemp(prefix="luthien_mock_e2e_")
    db_path = os.path.join(tmp_dir, "test.db")
    db_pool = DatabasePool(f"sqlite:///{db_path}")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(check_migrations(db_pool))

    # Configure auth for mock e2e: passthrough so the client's bearer becomes
    # the forwarding credential (which judge policies read via UserCredentials
    # auth provider), and validate_credentials=0 so the gateway does not probe
    # the mock backend's /count_tokens endpoint (which the mock server does not
    # implement). Without this, judge policies fail with "No user credential
    # on request context" because BOTH mode attaches no user_credential when
    # the bearer matches CLIENT_API_KEY.
    _conn = sqlite3.connect(db_path)
    try:
        _cur = _conn.execute("UPDATE auth_config SET auth_mode = 'passthrough', validate_credentials = 0 WHERE id = 1")
        if _cur.rowcount != 1:
            raise RuntimeError(
                f"auth_config seed row missing after migrations (rowcount={_cur.rowcount}); "
                "migration 007 should have INSERTed id=1"
            )
        _conn.commit()
    finally:
        _conn.close()

    os.environ["ANTHROPIC_BASE_URL"] = f"http://localhost:{mock_port}"
    os.environ["ANTHROPIC_API_KEY"] = "mock-key"

    app = create_app(
        api_key=api_key,
        admin_key=admin_api_key,
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path="config/policy_config.yaml",
        policy_source="db-fallback-file",
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=gateway_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="mock-gateway")
    thread.start()

    # Wait for gateway to be ready
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", gateway_port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        print("ERROR: Gateway did not start", file=sys.stderr)
        sys.exit(1)

    # Print config for the calling script
    info = {
        "gateway_url": f"http://127.0.0.1:{gateway_port}",
        "api_key": api_key,
        "admin_api_key": admin_api_key,
        "mock_port": mock_port,
    }
    print(json.dumps(info))
    sys.stdout.flush()

    # Block until signaled
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    server.should_exit = True
    thread.join(timeout=5)
    loop.run_until_complete(db_pool.close())
    loop.close()
    shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
