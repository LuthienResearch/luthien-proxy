"""Demo recording runtime: mock backend + reconfigured gateway.

Reuses the existing test mock infra (`MockAnthropicServer`) so the gateway's
upstream calls during demo recording are deterministic and free.

What it does:
  1. Starts MockAnthropicServer on port 18888.
  2. Rewrites ~/.luthien/luthien-proxy/.env to point ANTHROPIC_BASE_URL at the mock.
  3. Restarts the gateway via `luthien down && luthien up`.
  4. Stores a server_key credential ("anthropic", value="mock-key") so judge
     policies can resolve auth without reaching real Anthropic.
  5. Pre-programs the mock response queue for the canonical demo prompts.
  6. Sets SimpleLLMPolicy with a pip-rewrite judge config that hits the mock.
  7. Blocks on SIGINT/SIGTERM.
  8. On exit: reverts the .env changes, drops the server credential, restarts
     the gateway, and stops the mock.

Run from the repo root:
    uv run python assets/demos/mock_runtime.py

Then in a separate shell, render demo tapes:
    vhs assets/demos/with-luthien-prefer-uv.tape
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_mock_modules():
    """Load mock_anthropic.{responses,server} by file path.

    Importing them via package path doesn't work because `tests/luthien_proxy/`
    has no __init__.py and `src/luthien_proxy/` is a regular package, so the
    namespace-package merge that pytest relies on doesn't happen here.
    """
    import importlib.util

    mock_dir = REPO / "tests" / "luthien_proxy" / "e2e_tests" / "mock_anthropic"
    modules = {}
    for name in ("responses", "server"):
        spec = importlib.util.spec_from_file_location(
            f"_demo_mock_{name}", mock_dir / f"{name}.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        # `server.py` imports from `responses` via package-relative path; fix that
        # by injecting our already-loaded `responses` under the expected name.
        if name == "server" and "responses" in modules:
            sys.modules["tests.luthien_proxy.e2e_tests.mock_anthropic.responses"] = modules["responses"]
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


_mock = _load_mock_modules()
text_response = _mock["responses"].text_response
MockAnthropicServer = _mock["server"].MockAnthropicServer

# The mock's port.  Picked deliberately, not auto-allocated, so tape configs
# can hardcode it.
MOCK_PORT = 18888
MOCK_URL = f"http://localhost:{MOCK_PORT}"

ENV_PATH = Path.home() / ".luthien" / "luthien-proxy" / ".env"
ENV_BACKUP = ENV_PATH.parent / ".env.demo-backup"

# Default response if no scripted match.  Exact tape prompts get specific
# responses pre-enqueued in `seed_responses()`.
DEFAULT_RESPONSE = "`pip install requests`"


def patch_env() -> None:
    """Back up the current .env and rewrite it to point upstream at the mock."""
    if not ENV_PATH.exists():
        raise SystemExit(f"Gateway .env not found at {ENV_PATH}.  Run `luthien onboard` first.")

    if ENV_BACKUP.exists():
        # A previous run was interrupted before restore.  Refuse to overwrite the
        # backup (which would make restore impossible).  Operator decides what to do.
        raise SystemExit(
            f"Stale backup at {ENV_BACKUP}.  Either restore it manually "
            f"(`mv {ENV_BACKUP} {ENV_PATH}`) or delete it if you've already restored."
        )

    ENV_BACKUP.write_text(ENV_PATH.read_text())
    content = ENV_PATH.read_text()

    overrides: dict[str, str] = {
        "ANTHROPIC_BASE_URL": MOCK_URL,
        "AUTH_MODE": "passthrough",
        "VALIDATE_CREDENTIALS": "false",
    }
    for key, value in overrides.items():
        pattern = rf"^#?\s*{re.escape(key)}=.*$"
        replacement = f"{key}={value}"
        content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)
        if count == 0:
            content = content.rstrip() + f"\n{replacement}\n"

    ENV_PATH.write_text(content)
    os.chmod(ENV_PATH, 0o600)


def restore_env() -> None:
    if ENV_BACKUP.exists():
        ENV_PATH.write_text(ENV_BACKUP.read_text())
        os.chmod(ENV_PATH, 0o600)
        ENV_BACKUP.unlink()


def restart_gateway() -> None:
    subprocess.run(["luthien", "down"], check=False, capture_output=True)
    time.sleep(0.5)
    result = subprocess.run(["luthien", "up"], check=True, capture_output=True, text=True)
    print(result.stdout.strip(), file=sys.stderr)


def _admin_key() -> str:
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith("ADMIN_API_KEY="):
            return line.split("=", 1)[1]
    raise SystemExit("ADMIN_API_KEY not found in gateway .env")


def _gateway_url() -> str:
    config_path = Path.home() / ".luthien" / "config.toml"
    for line in config_path.read_text().splitlines():
        if "url" in line and "=" in line:
            return line.split("=", 1)[1].strip().strip('"')
    return "http://localhost:8000"


def _admin_post(path: str, body: dict) -> dict:
    import httpx

    r = httpx.post(
        f"{_gateway_url()}{path}",
        headers={"Authorization": f"Bearer {_admin_key()}"},
        json=body,
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()


def store_mock_credential() -> None:
    _admin_post(
        "/api/admin/credentials",
        {"name": "anthropic", "value": "mock-key", "credential_type": "api_key", "platform": "anthropic"},
    )


def set_pip_to_uv_policy() -> None:
    """StringReplacementPolicy doing pip→uv on the response side.

    Chosen over SimpleLLMPolicy because:
      - It's a single-request flow.  Interactive Claude Code's TUI sends extra
        pre/post requests that consumed the queued judge response out-of-order
        when we tried the judge approach.
      - Deterministic — no second LLM call to mock and parse.
      - Zero auth concerns — no judge credential needed.
    """
    config = {
        "replacements": [["pip install", "uv pip install"]],
        "apply_to": "response",
    }
    _admin_post(
        "/api/admin/policy/set",
        {
            "policy_class_ref": "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
            "config": config,
            "enabled_by": "demo-runtime",
        },
    )


def seed_responses(mock: MockAnthropicServer) -> None:
    """Pre-enqueue one response per take.  StringReplacementPolicy doesn't
    make a second LLM call, so we only need one slot per claude-p invocation.

    For interactive Claude TUI takes the queue may need 2-3 slots (TUI does
    pre-flight requests).  Set a generous default so unexpected requests still
    get a sensible response.

    Re-seed between takes (`kill -USR1 $(pgrep -f mock_runtime.py)`).
    """
    mock.enqueue(text_response("`pip install requests`"))
    mock.set_default(text_response("`pip install requests`"))


def main() -> None:
    print(f"[mock_runtime] backing up {ENV_PATH} -> {ENV_BACKUP}", file=sys.stderr)
    patch_env()

    print(f"[mock_runtime] starting MockAnthropicServer on port {MOCK_PORT}", file=sys.stderr)
    mock = MockAnthropicServer(port=MOCK_PORT)
    mock.start()

    try:
        print("[mock_runtime] restarting gateway with mock-pointed env", file=sys.stderr)
        restart_gateway()

        print("[mock_runtime] storing mock server credential", file=sys.stderr)
        store_mock_credential()

        print("[mock_runtime] activating SimpleLLMPolicy (pip→uv via mock judge)", file=sys.stderr)
        set_pip_to_uv_policy()

        print("[mock_runtime] seeding response queue", file=sys.stderr)
        seed_responses(mock)

        print(
            json.dumps(
                {
                    "status": "ready",
                    "mock_url": MOCK_URL,
                    "gateway_url": _gateway_url(),
                    "received_requests": mock.received_requests(),
                }
            ),
            flush=True,
        )
        print(
            "\n[mock_runtime] Gateway upstream is now the mock backend.\n"
            "  Re-seed the queue between takes with: kill -USR1 $(pgrep -f mock_runtime.py)\n"
            "  Tear down: Ctrl-C\n",
            file=sys.stderr,
        )

        stop = threading.Event()

        def _stop_handler(_signum, _frame):
            stop.set()

        def _reseed_handler(_signum, _frame):
            mock.drain_queue()
            mock.clear_requests()
            seed_responses(mock)
            print("[mock_runtime] queue reseeded", file=sys.stderr)

        signal.signal(signal.SIGINT, _stop_handler)
        signal.signal(signal.SIGTERM, _stop_handler)
        signal.signal(signal.SIGUSR1, _reseed_handler)

        stop.wait()

    finally:
        print("\n[mock_runtime] tearing down", file=sys.stderr)
        try:
            mock.stop()
        except Exception as exc:
            print(f"[mock_runtime] mock.stop() failed: {exc}", file=sys.stderr)
        restore_env()
        print("[mock_runtime] restarting gateway with restored env", file=sys.stderr)
        try:
            restart_gateway()
        except Exception as exc:
            print(f"[mock_runtime] gateway restart failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
