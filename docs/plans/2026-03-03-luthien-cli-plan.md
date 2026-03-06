# Luthien CLI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a standalone, pipx-installable CLI tool (`luthien`) for managing and interacting with luthien-proxy gateways.

**Architecture:** Thin client CLI that talks to gateways over HTTP (httpx), manages config in `~/.luthien/config.toml`, and optionally manages a local docker-compose stack via subprocess. No dependency on the luthien-proxy server package.

**Tech Stack:** Python 3.13, Click (CLI framework), httpx (HTTP client), tomli/tomli-w (TOML config), rich (terminal output)

---

### Task 1: Scaffold the package

**Files:**
- Create: `luthien-cli/pyproject.toml`
- Create: `luthien-cli/src/luthien_cli/__init__.py`
- Create: `luthien-cli/src/luthien_cli/main.py`

**Step 1: Create the package directory structure**

```bash
mkdir -p luthien-cli/src/luthien_cli/commands
mkdir -p luthien-cli/tests
```

**Step 2: Write `pyproject.toml`**

```toml
[project]
name = "luthien-cli"
version = "0.1.0"
description = "CLI tool for managing luthien-proxy gateways"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1.0",
    "httpx>=0.27.0",
    "tomli>=2.0.0;python_version<'3.11'",
    "tomli-w>=1.0.0",
    "rich>=13.0.0",
]

[project.scripts]
luthien = "luthien_cli.main:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/luthien_cli"]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.0",
]
```

**Step 3: Write `__init__.py`**

```python
"""Luthien CLI — manage and interact with luthien-proxy gateways."""
```

**Step 4: Write minimal `main.py` with click group**

```python
"""CLI entry point."""

import click


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Luthien — manage and interact with luthien-proxy gateways."""


if __name__ == "__main__":
    cli()
```

**Step 5: Verify install works**

```bash
cd luthien-cli && pip install -e . && luthien --version
```

Expected: `luthien, version 0.1.0`

**Step 6: Commit**

```bash
git add luthien-cli/
git commit -m "feat(cli): scaffold luthien-cli package"
```

---

### Task 2: Config module (`~/.luthien/config.toml`)

**Files:**
- Create: `luthien-cli/src/luthien_cli/config.py`
- Create: `luthien-cli/tests/test_config.py`

**Step 1: Write the failing tests**

```python
"""Tests for config module."""

import os
from pathlib import Path

import pytest

from luthien_cli.config import LuthienConfig, load_config, save_config


@pytest.fixture
def config_dir(tmp_path):
    """Use a temp dir for config instead of ~/.luthien/."""
    config_path = tmp_path / "config.toml"
    return tmp_path, config_path


def test_load_config_returns_defaults_when_no_file(config_dir):
    config_dir_path, config_path = config_dir
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
```

**Step 2: Run tests to verify they fail**

```bash
cd luthien-cli && python -m pytest tests/test_config.py -v
```

Expected: ImportError

**Step 3: Implement config.py**

```python
"""Config management for ~/.luthien/config.toml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

try:
    import tomllib
except ImportError:
    import tomli as tomllib


DEFAULT_CONFIG_DIR = Path.home() / ".luthien"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"


@dataclass
class LuthienConfig:
    gateway_url: str = "http://localhost:8000"
    api_key: str | None = None
    admin_key: str | None = None
    repo_path: str | None = None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LuthienConfig:
    """Load config from TOML file. Returns defaults if file doesn't exist."""
    if not path.exists():
        return LuthienConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    gateway = data.get("gateway", {})
    local = data.get("local", {})

    return LuthienConfig(
        gateway_url=gateway.get("url", "http://localhost:8000"),
        api_key=gateway.get("api_key"),
        admin_key=gateway.get("admin_key"),
        repo_path=local.get("repo_path"),
    )


def save_config(config: LuthienConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Save config to TOML file. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "gateway": {
            "url": config.gateway_url,
        },
        "local": {},
    }
    if config.api_key:
        data["gateway"]["api_key"] = config.api_key
    if config.admin_key:
        data["gateway"]["admin_key"] = config.admin_key
    if config.repo_path:
        data["local"]["repo_path"] = config.repo_path

    with open(path, "wb") as f:
        tomli_w.dump(data, f)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_config.py -v
```

Expected: All pass

**Step 5: Commit**

```bash
git add luthien-cli/src/luthien_cli/config.py luthien-cli/tests/test_config.py
git commit -m "feat(cli): add config module for ~/.luthien/config.toml"
```

---

### Task 3: Gateway client (`gateway_client.py`)

**Files:**
- Create: `luthien-cli/src/luthien_cli/gateway_client.py`
- Create: `luthien-cli/tests/test_gateway_client.py`

**Step 1: Write failing tests**

Tests should mock httpx responses. Key behaviors:
- `health()` — GET `/health`, return parsed JSON or raise on connection error
- `get_current_policy()` — GET `/api/admin/policy/current` with admin key header
- `get_auth_config()` — GET `/api/admin/auth/config` with admin key header

```python
"""Tests for gateway client."""

import httpx
import pytest

from luthien_cli.gateway_client import GatewayClient, GatewayError


@pytest.fixture
def client():
    return GatewayClient(
        base_url="http://localhost:8000",
        api_key="sk-test",
        admin_key="admin-test",
    )


def test_health_success(client, httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:8000/health",
        json={"status": "healthy", "version": "2.0.0"},
    )
    result = client.health()
    assert result["status"] == "healthy"


def test_health_connection_error(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(GatewayError, match="Cannot connect"):
        client.health()


def test_get_current_policy(client, httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:8000/api/admin/policy/current",
        json={
            "policy": "NoOpPolicy",
            "class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "enabled_at": "2026-03-03T10:00:00",
            "enabled_by": "api",
            "config": {},
        },
    )
    result = client.get_current_policy()
    assert result["policy"] == "NoOpPolicy"


def test_get_auth_config(client, httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:8000/api/admin/auth/config",
        json={"auth_mode": "both", "validate_credentials": True,
              "valid_cache_ttl_seconds": 300, "invalid_cache_ttl_seconds": 60},
    )
    result = client.get_auth_config()
    assert result["auth_mode"] == "both"
```

Note: Add `pytest-httpx` to dev dependencies in pyproject.toml.

**Step 2: Run tests to verify they fail**

```bash
cd luthien-cli && python -m pytest tests/test_gateway_client.py -v
```

**Step 3: Implement gateway_client.py**

```python
"""HTTP client for luthien-proxy gateway APIs."""

from __future__ import annotations

from typing import Any

import httpx


class GatewayError(Exception):
    """Error communicating with gateway."""


class GatewayClient:
    """Thin HTTP client for gateway admin/health APIs."""

    def __init__(self, base_url: str, api_key: str | None = None, admin_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.admin_key = admin_key

    def _admin_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.admin_key:
            headers["x-admin-key"] = self.admin_key
        return headers

    def _get(self, path: str, admin: bool = False) -> dict[str, Any]:
        headers = self._admin_headers() if admin else {}
        try:
            response = httpx.get(f"{self.base_url}{path}", headers=headers, timeout=10.0)
        except httpx.ConnectError:
            raise GatewayError(f"Cannot connect to gateway at {self.base_url}")
        except httpx.TimeoutException:
            raise GatewayError(f"Gateway at {self.base_url} timed out")

        if response.status_code == 401:
            raise GatewayError("Authentication failed — check your admin_key")
        if response.status_code == 403:
            raise GatewayError("Forbidden — admin access required")
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def get_current_policy(self) -> dict[str, Any]:
        return self._get("/api/admin/policy/current", admin=True)

    def get_auth_config(self) -> dict[str, Any]:
        return self._get("/api/admin/auth/config", admin=True)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_gateway_client.py -v
```

Expected: All pass

**Step 5: Commit**

```bash
git add luthien-cli/src/luthien_cli/gateway_client.py luthien-cli/tests/test_gateway_client.py luthien-cli/pyproject.toml
git commit -m "feat(cli): add gateway HTTP client"
```

---

### Task 4: `luthien status` command

**Files:**
- Create: `luthien-cli/src/luthien_cli/commands/status.py`
- Create: `luthien-cli/src/luthien_cli/commands/__init__.py`
- Modify: `luthien-cli/src/luthien_cli/main.py` (register command)
- Create: `luthien-cli/tests/test_status.py`

**Step 1: Write failing test**

Use Click's `CliRunner` to invoke the command. Mock the gateway client.

```python
"""Tests for status command."""

from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.main import cli


def test_status_shows_healthy_gateway():
    runner = CliRunner()
    with patch("luthien_cli.commands.status.make_client") as mock_client:
        client = mock_client.return_value
        client.health.return_value = {"status": "healthy", "version": "2.0.0"}
        client.get_current_policy.return_value = {
            "policy": "NoOpPolicy",
            "class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "enabled_at": "2026-03-03T10:00:00",
            "enabled_by": "api",
            "config": {},
        }
        client.get_auth_config.return_value = {
            "auth_mode": "both",
            "validate_credentials": True,
            "valid_cache_ttl_seconds": 300,
            "invalid_cache_ttl_seconds": 60,
        }
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "healthy" in result.output
        assert "NoOpPolicy" in result.output


def test_status_shows_unreachable_gateway():
    runner = CliRunner()
    with patch("luthien_cli.commands.status.make_client") as mock_client:
        from luthien_cli.gateway_client import GatewayError
        client = mock_client.return_value
        client.health.side_effect = GatewayError("Cannot connect")
        result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0 or "Cannot connect" in result.output
```

**Step 2: Run test to verify it fails**

**Step 3: Implement status command**

`commands/__init__.py`: empty file

`commands/status.py`:

```python
"""luthien status — show gateway state."""

import click
from rich.console import Console
from rich.table import Table

from luthien_cli.config import load_config
from luthien_cli.gateway_client import GatewayClient, GatewayError


def make_client() -> GatewayClient:
    config = load_config()
    return GatewayClient(
        base_url=config.gateway_url,
        api_key=config.api_key,
        admin_key=config.admin_key,
    )


@click.command()
def status():
    """Show gateway health, active policy, and auth mode."""
    console = Console()
    client = make_client()

    try:
        health = client.health()
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    table = Table(title="Gateway Status")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("URL", client.base_url)
    table.add_row("Status", f"[green]{health['status']}[/green]")
    table.add_row("Version", health.get("version", "unknown"))

    try:
        policy = client.get_current_policy()
        table.add_row("Policy", policy["policy"])
        table.add_row("Policy Class", policy["class_ref"])
    except GatewayError:
        table.add_row("Policy", "[yellow]unavailable (no admin key?)[/yellow]")

    try:
        auth = client.get_auth_config()
        table.add_row("Auth Mode", auth["auth_mode"])
    except GatewayError:
        table.add_row("Auth Mode", "[yellow]unavailable[/yellow]")

    console.print(table)
```

Update `main.py` to register:

```python
from luthien_cli.commands.status import status

cli.add_command(status)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_status.py -v
```

**Step 5: Manual test (if gateway is running)**

```bash
luthien status
```

**Step 6: Commit**

```bash
git add luthien-cli/src/luthien_cli/commands/ luthien-cli/src/luthien_cli/main.py luthien-cli/tests/test_status.py
git commit -m "feat(cli): add luthien status command"
```

---

### Task 5: `luthien claude` command

**Files:**
- Create: `luthien-cli/src/luthien_cli/commands/claude.py`
- Create: `luthien-cli/tests/test_claude.py`
- Modify: `luthien-cli/src/luthien_cli/main.py` (register command)

**Step 1: Write failing test**

```python
"""Tests for claude command."""

import os
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from luthien_cli.main import cli


def test_claude_sets_env_and_execs(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gateway]\nurl = "http://localhost:9000"\napi_key = "sk-proxy"\n'
    )
    with patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path), \
         patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"), \
         patch("os.execvpe") as mock_exec:
        result = runner.invoke(cli, ["claude", "--", "--model", "opus"])
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][0] == "claude"
        env = call_args[0][2]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:9000/"
        assert env["ANTHROPIC_API_KEY"] == "sk-proxy"


def test_claude_fails_when_not_installed(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\napi_key = "sk-test"\n')
    with patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path), \
         patch("luthien_cli.commands.claude.shutil.which", return_value=None):
        result = runner.invoke(cli, ["claude"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "not installed" in result.output.lower()
```

**Step 2: Run tests to verify they fail**

**Step 3: Implement claude command**

```python
"""luthien claude — launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


@click.command(context_settings={"ignore_unknown_options": True})
@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)
def claude(claude_args: tuple[str, ...]):
    """Launch Claude Code routed through the configured gateway.

    All arguments after 'claude' are passed through to Claude Code.
    """
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.api_key:
        console.print("[red]No API key configured. Run: luthien config set gateway.api_key <key>[/red]")
        raise SystemExit(1)

    claude_path = shutil.which("claude")
    if not claude_path:
        console.print("[red]Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-cli[/red]")
        raise SystemExit(1)

    gateway_url = config.gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url
    env["ANTHROPIC_API_KEY"] = config.api_key

    console.print(f"[blue]Routing Claude Code through {config.gateway_url}[/blue]")

    os.execvpe("claude", ["claude", *claude_args], env)
```

Register in `main.py`:
```python
from luthien_cli.commands.claude import claude
cli.add_command(claude)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_claude.py -v
```

**Step 5: Commit**

```bash
git add luthien-cli/src/luthien_cli/commands/claude.py luthien-cli/tests/test_claude.py luthien-cli/src/luthien_cli/main.py
git commit -m "feat(cli): add luthien claude command"
```

---

### Task 6: `luthien up` / `luthien down` commands

**Files:**
- Create: `luthien-cli/src/luthien_cli/commands/up.py`
- Create: `luthien-cli/tests/test_up.py`
- Modify: `luthien-cli/src/luthien_cli/main.py` (register commands)

**Step 1: Write failing tests**

```python
"""Tests for up/down commands."""

from unittest.mock import patch, MagicMock, call
from pathlib import Path

from click.testing import CliRunner

from luthien_cli.main import cli


def test_up_runs_docker_compose(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path), \
         patch("luthien_cli.commands.up.subprocess.run") as mock_run, \
         patch("luthien_cli.commands.up.wait_for_healthy", return_value=True):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        mock_run.assert_called()


def test_up_fails_without_repo_path(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    with patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["up"], input="\n")
        assert "repo_path" in result.output.lower() or "repo" in result.output.lower()


def test_down_runs_docker_compose_down(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path), \
         patch("luthien_cli.commands.up.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["down"])
        assert result.exit_code == 0
```

**Step 2: Run tests to verify they fail**

**Step 3: Implement up.py**

```python
"""luthien up/down — manage local docker-compose stack."""

from __future__ import annotations

import subprocess
import time

import click
import httpx
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config


def wait_for_healthy(url: str, timeout: int = 60) -> bool:
    """Poll gateway /health until it responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=5.0)
            if r.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(2)
    return False


@click.command()
@click.option("--follow", "-f", is_flag=True, help="Tail gateway logs after startup")
def up(follow: bool):
    """Start the local luthien-proxy stack (db, redis, gateway)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        repo = click.prompt("Path to luthien-proxy repo", type=str)
        config.repo_path = repo
        save_config(config, DEFAULT_CONFIG_PATH)

    console.print(f"[blue]Starting stack in {config.repo_path}[/blue]")

    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=config.repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
        raise SystemExit(1)

    console.print("[yellow]Waiting for gateway to be healthy...[/yellow]")
    if wait_for_healthy(config.gateway_url):
        console.print(f"[green]Gateway is healthy at {config.gateway_url}[/green]")
    else:
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        raise SystemExit(1)

    if follow:
        subprocess.run(
            ["docker", "compose", "logs", "-f", "gateway"],
            cwd=config.repo_path,
        )


@click.command()
def down():
    """Stop the local luthien-proxy stack."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Nothing to stop.[/red]")
        raise SystemExit(1)

    console.print(f"[blue]Stopping stack in {config.repo_path}[/blue]")

    result = subprocess.run(
        ["docker", "compose", "down"],
        cwd=config.repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]docker compose down failed:[/red]\n{result.stderr}")
        raise SystemExit(1)

    console.print("[green]Stack stopped.[/green]")
```

Register both in `main.py`:
```python
from luthien_cli.commands.up import up, down
cli.add_command(up)
cli.add_command(down)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_up.py -v
```

**Step 5: Commit**

```bash
git add luthien-cli/src/luthien_cli/commands/up.py luthien-cli/tests/test_up.py luthien-cli/src/luthien_cli/main.py
git commit -m "feat(cli): add luthien up/down commands"
```

---

### Task 7: `luthien logs` command

**Files:**
- Create: `luthien-cli/src/luthien_cli/commands/logs.py`
- Create: `luthien-cli/tests/test_logs.py`
- Modify: `luthien-cli/src/luthien_cli/main.py` (register command)

**Step 1: Write failing test**

```python
"""Tests for logs command."""

from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from luthien_cli.main import cli


def test_logs_runs_docker_compose_logs(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path), \
         patch("luthien_cli.commands.logs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code == 0
        args = mock_run.call_args[0][0]
        assert "docker" in args
        assert "logs" in args


def test_logs_with_tail(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path), \
         patch("luthien_cli.commands.logs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["logs", "--tail", "50"])
        assert result.exit_code == 0
        args = mock_run.call_args[0][0]
        assert "--tail" in args
        assert "50" in args
```

**Step 2: Run tests to verify they fail**

**Step 3: Implement logs.py**

```python
"""luthien logs — view gateway logs."""

from __future__ import annotations

import subprocess

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


@click.command()
@click.option("--tail", "-n", default=None, type=int, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
def logs(tail: int | None, follow: bool):
    """View gateway logs (requires local repo_path configured)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Set it with: luthien config set local.repo_path <path>[/red]")
        raise SystemExit(1)

    cmd = ["docker", "compose", "logs", "gateway"]
    if tail is not None:
        cmd.extend(["--tail", str(tail)])
    if follow:
        cmd.append("-f")

    subprocess.run(cmd, cwd=config.repo_path)
```

Register in `main.py`:
```python
from luthien_cli.commands.logs import logs
cli.add_command(logs)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_logs.py -v
```

**Step 5: Commit**

```bash
git add luthien-cli/src/luthien_cli/commands/logs.py luthien-cli/tests/test_logs.py luthien-cli/src/luthien_cli/main.py
git commit -m "feat(cli): add luthien logs command"
```

---

### Task 8: `luthien config` command

**Files:**
- Create: `luthien-cli/src/luthien_cli/commands/config_cmd.py`
- Create: `luthien-cli/tests/test_config_cmd.py`
- Modify: `luthien-cli/src/luthien_cli/main.py` (register command)

**Step 1: Write failing test**

```python
"""Tests for config command."""

from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.main import cli


def test_config_show_displays_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\napi_key = "sk-test"\n')
    runner = CliRunner()
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "localhost:9000" in result.output


def test_config_set_updates_value(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    runner = CliRunner()
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "set", "gateway.url", "http://remote:9000"])
        assert result.exit_code == 0
    with patch("luthien_cli.commands.config_cmd.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["config", "show"])
        assert "remote:9000" in result.output
```

**Step 2: Run tests to verify they fail**

**Step 3: Implement config_cmd.py**

```python
"""luthien config — view and edit CLI configuration."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config


@click.group()
def config():
    """View or edit luthien CLI configuration."""


@config.command()
def show():
    """Display current configuration."""
    console = Console()
    cfg = load_config(DEFAULT_CONFIG_PATH)

    table = Table(title=f"Config ({DEFAULT_CONFIG_PATH})")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("gateway.url", cfg.gateway_url)
    table.add_row("gateway.api_key", _mask(cfg.api_key))
    table.add_row("gateway.admin_key", _mask(cfg.admin_key))
    table.add_row("local.repo_path", cfg.repo_path or "[dim]not set[/dim]")

    console.print(table)


@config.command("set")
@click.argument("key")
@click.argument("value")
def set_value(key: str, value: str):
    """Set a config value. Keys: gateway.url, gateway.api_key, gateway.admin_key, local.repo_path"""
    console = Console()
    cfg = load_config(DEFAULT_CONFIG_PATH)

    field_map = {
        "gateway.url": "gateway_url",
        "gateway.api_key": "api_key",
        "gateway.admin_key": "admin_key",
        "local.repo_path": "repo_path",
    }

    if key not in field_map:
        console.print(f"[red]Unknown key: {key}[/red]")
        console.print(f"Valid keys: {', '.join(field_map.keys())}")
        raise SystemExit(1)

    setattr(cfg, field_map[key], value)
    save_config(cfg, DEFAULT_CONFIG_PATH)
    console.print(f"[green]Set {key} = {value}[/green]")


def _mask(value: str | None) -> str:
    if not value:
        return "[dim]not set[/dim]"
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]
```

Register in `main.py`:
```python
from luthien_cli.commands.config_cmd import config
cli.add_command(config)
```

**Step 4: Run tests**

```bash
cd luthien-cli && python -m pytest tests/test_config_cmd.py -v
```

**Step 5: Commit**

```bash
git add luthien-cli/src/luthien_cli/commands/config_cmd.py luthien-cli/tests/test_config_cmd.py luthien-cli/src/luthien_cli/main.py
git commit -m "feat(cli): add luthien config show/set commands"
```

---

### Task 9: Integration test & final polish

**Files:**
- Modify: `luthien-cli/src/luthien_cli/main.py` (ensure all commands registered cleanly)
- Create: `luthien-cli/tests/test_integration.py`
- Create: `luthien-cli/README.md`

**Step 1: Write integration test**

```python
"""Integration test — verify all commands are registered and --help works."""

from click.testing import CliRunner

from luthien_cli.main import cli


def test_all_commands_registered():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ["status", "claude", "up", "down", "logs", "config"]:
        assert cmd in result.output, f"Command '{cmd}' not in help output"


def test_each_command_has_help():
    runner = CliRunner()
    for cmd in ["status", "claude", "up", "down", "logs", "config"]:
        result = runner.invoke(cli, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed"
```

**Step 2: Run full test suite**

```bash
cd luthien-cli && python -m pytest tests/ -v
```

**Step 3: Write README.md**

Brief usage docs: install, configure, command reference.

**Step 4: Run `pipx install .` from luthien-cli/ to verify end-to-end**

```bash
cd luthien-cli && pipx install .
luthien --help
luthien config show
```

**Step 5: Commit**

```bash
git add luthien-cli/
git commit -m "feat(cli): integration tests and README"
```

---

### Task 10: Push and open PR

**Step 1: Run all tests one final time**

```bash
cd luthien-cli && python -m pytest tests/ -v --tb=short
```

**Step 2: Push and create PR**

```bash
git push -u origin <branch>
gh pr create --draft --title "feat: add luthien CLI tool" --body "..."
```
