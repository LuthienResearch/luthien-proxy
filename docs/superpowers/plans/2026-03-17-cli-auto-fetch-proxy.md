# CLI Auto-Fetch Proxy Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `luthien onboard` auto-download proxy artifacts from GitHub so users don't need a pre-existing repo checkout.

**Architecture:** New `repo.py` module manages a `~/.luthien/luthien-proxy/` directory, downloading `docker-compose.yaml` and `.env.example` from GitHub raw. `onboard.py` and `up.py` call `ensure_repo()` instead of prompting for a path.

**Tech Stack:** Python 3.11+, httpx, click, pytest, pytest-httpx

**Spec:** `docs/superpowers/specs/2026-03-17-cli-auto-fetch-proxy-design.md`

---

### Task 1: Create `repo.py` with tests

**Files:**
- Create: `src/luthien_cli/src/luthien_cli/repo.py`
- Create: `src/luthien_cli/tests/test_repo.py`

- [ ] **Step 1: Write tests for `_get_remote_sha()`**

```python
# src/luthien_cli/tests/test_repo.py
"""Tests for repo module -- managed proxy artifact directory."""

import pytest
import httpx

from luthien_cli.repo import (
    GITHUB_RAW_BASE,
    GITHUB_SHA_URL,
    MANAGED_REPO_DIR,
    _get_remote_sha,
    _download_files,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_repo.py -v`
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: Write tests for `_strip_dev_only_lines()`**

Add to `test_repo.py`:

```python
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
```

- [ ] **Step 4: Write tests for `_download_files()`**

Add to `test_repo.py`:

```python
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
```

- [ ] **Step 5: Write tests for `ensure_repo()`**

Add to `test_repo.py`:

```python
from unittest.mock import patch


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
            result = ensure_repo()

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
```

- [ ] **Step 6: Implement `repo.py`**

```python
# src/luthien_cli/src/luthien_cli/repo.py
"""Managed proxy artifact directory at ~/.luthien/luthien-proxy/."""

from __future__ import annotations

import re
from pathlib import Path

import click
import httpx
from rich.console import Console

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/"
GITHUB_SHA_URL = "https://api.github.com/repos/LuthienResearch/luthien-proxy/commits/main"
MANAGED_REPO_DIR = Path.home() / ".luthien" / "luthien-proxy"

FILES_TO_DOWNLOAD = ("docker-compose.yaml", ".env.example")

# Matches build: blocks (build: + indented context/dockerfile lines)
_BUILD_BLOCK_RE = re.compile(r"^ +build:\n(?: +\w.*\n)*", re.MULTILINE)
# Matches the ./src volume mount line
_SRC_MOUNT_RE = re.compile(r"^ *- \./src:/app/src.*\n", re.MULTILINE)


def _get_remote_sha() -> str:
    """Get the latest commit SHA of the main branch."""
    r = httpx.get(
        GITHUB_SHA_URL,
        headers={"Accept": "application/vnd.github.sha"},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.text.strip()


def _strip_dev_only_lines(content: str) -> str:
    """Remove dev-only lines from docker-compose.yaml.

    Strips the ./src volume mount (source is baked into the GHCR image)
    and build: blocks (no local Dockerfiles in managed install).
    """
    content = _BUILD_BLOCK_RE.sub("", content)
    content = _SRC_MOUNT_RE.sub("", content)
    return content


def _download_files(dest: Path) -> None:
    """Download proxy artifacts from GitHub and write to dest."""
    console = Console(stderr=True)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "config").mkdir(exist_ok=True)

    for filename in FILES_TO_DOWNLOAD:
        url = f"{GITHUB_RAW_BASE}{filename}"
        try:
            r = httpx.get(url, timeout=15.0, follow_redirects=True)
            r.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            console.print(
                f"[red]Could not download {filename} from GitHub. "
                f"Check your internet connection.[/red]\n[dim]{e}[/dim]"
            )
            raise SystemExit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red]Failed to download {url}: HTTP {e.response.status_code}[/red]")
            raise SystemExit(1)

        content = r.text
        if filename == "docker-compose.yaml":
            content = _strip_dev_only_lines(content)

        (dest / filename).write_text(content)

    try:
        sha = _get_remote_sha()
    except (httpx.HTTPError, httpx.TimeoutException):
        sha = "unknown"
    (dest / ".version").write_text(sha)


def ensure_repo() -> str:
    """Ensure managed proxy directory exists and is up to date. Returns path."""
    console = Console(stderr=True)
    dest = MANAGED_REPO_DIR

    has_version = (dest / ".version").is_file()
    has_compose = (dest / "docker-compose.yaml").is_file()

    if has_version and has_compose:
        local_sha = (dest / ".version").read_text().strip()
        try:
            remote_sha = _get_remote_sha()
        except (httpx.HTTPError, httpx.TimeoutException):
            console.print("[yellow]Could not check for updates (network error). Using existing files.[/yellow]")
            return str(dest)

        if remote_sha == local_sha:
            return str(dest)

        if click.confirm("A newer version of luthien-proxy is available. Update?", default=True):
            console.print("[blue]Updating proxy files...[/blue]")
            _download_files(dest)
        return str(dest)

    console.print("[blue]Downloading luthien-proxy files...[/blue]")
    _download_files(dest)
    return str(dest)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_repo.py -v`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/repo.py src/luthien_cli/tests/test_repo.py
git commit -m "feat(cli): add repo module for managed proxy artifact directory"
```

---

### Task 2: Update `onboard.py` to use `ensure_repo()`

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/commands/onboard.py`
- Modify: `src/luthien_cli/tests/test_onboard.py`

- [ ] **Step 1: Update tests for new onboard flow**

In `test_onboard.py`, the full-flow test currently provides a repo path via stdin input. Update it to mock `ensure_repo()` instead:

```python
# Replace the test_onboard_full_flow test:
def test_onboard_full_flow(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "docker-compose.yaml").touch()
    (repo_path / ".env.example").write_text("PROXY_API_KEY=placeholder\n")

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_repo", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(
            cli,
            ["onboard"],
            input="Block PII from all responses\n",
        )

    assert result.exit_code == 0, result.output
    assert "Gateway is running" in result.output
    assert "luthien claude" in result.output

    policy = (repo_path / "config" / "policy_config.yaml").read_text()
    assert "Block PII from all responses" in policy
    assert config_path.exists()
```

Replace `test_onboard_docker_failure`:

```python
def test_onboard_docker_failure(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "docker-compose.yaml").touch()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_repo", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1, stderr="compose error")
        result = runner.invoke(cli, ["onboard"], input="Block PII\n")

    assert result.exit_code != 0
    assert "failed" in result.output.lower()
```

Replace `test_onboard_gateway_unhealthy`:

```python
def test_onboard_gateway_unhealthy(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "docker-compose.yaml").touch()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_repo", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["onboard"], input="Block PII\n")

    assert result.exit_code != 0
    assert "healthy" in result.output.lower()
```

Remove `test_onboard_rejects_invalid_repo` — this validation is now inside `ensure_repo()`.

Remove `_make_repo` helper — no longer needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_onboard.py -v`
Expected: Failures (onboard still prompts for repo path)

- [ ] **Step 3: Update `onboard.py`**

Changes to make:
1. Add import: `from luthien_cli.repo import ensure_repo`
2. Replace the repo_path prompt block (lines 105-114) with:
   ```python
   # 1. Ensure proxy files are available
   config.repo_path = ensure_repo()
   ```
3. Change docker compose command (line 143-148) from `["docker", "compose", "up", "-d", "--build"]` to two calls:
   ```python
   # Pull latest images
   pull_result = subprocess.run(
       ["docker", "compose", "pull"],
       cwd=config.repo_path,
       capture_output=True,
       text=True,
   )
   if pull_result.returncode != 0:
       console.print(f"[red]docker compose pull failed:[/red]\n{pull_result.stderr}")
       raise SystemExit(1)
   result = subprocess.run(
       ["docker", "compose", "up", "-d"],
       cwd=config.repo_path,
       capture_output=True,
       text=True,
   )
   ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_onboard.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/commands/onboard.py src/luthien_cli/tests/test_onboard.py
git commit -m "feat(cli): onboard auto-downloads proxy instead of requiring repo path"
```

---

### Task 3: Update `up.py` to use `ensure_repo()`

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/commands/up.py`
- Modify: `src/luthien_cli/tests/test_up.py`

- [ ] **Step 1: Read existing `test_up.py`**

Read `src/luthien_cli/tests/test_up.py` to understand current test structure.

- [ ] **Step 2: Update tests**

Update `test_up_prompts_for_repo_path_when_missing` — rename to `test_up_calls_ensure_repo_when_missing` and mock `ensure_repo` instead of providing input.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_up.py -v`
Expected: Failures

- [ ] **Step 4: Update `up.py`**

In the `up()` command, replace the repo_path prompt block (lines 36-39):
```python
if not config.repo_path:
    repo = click.prompt("Path to luthien-proxy repo", type=str)
    config.repo_path = repo
    save_config(config, DEFAULT_CONFIG_PATH)
```

With:
```python
if not config.repo_path:
    config.repo_path = ensure_repo()
    save_config(config, DEFAULT_CONFIG_PATH)
```

Add import: `from luthien_cli.repo import ensure_repo`

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_up.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/commands/up.py src/luthien_cli/tests/test_up.py
git commit -m "feat(cli): up command auto-downloads proxy when repo_path not set"
```

---

### Task 4: Update README and run full test suite

**Files:**
- Modify: `src/luthien_cli/README.md`

- [ ] **Step 1: Update README Quick Start**

Replace the Quick Start section (lines 15-35 of README.md) to remove the `repo_path` setup step. New content:

````markdown
## Quick Start

```bash
# Run the interactive setup (downloads proxy automatically)
luthien onboard

# Launch Claude Code through the proxy
luthien claude

# Check gateway status
luthien status

# Optional: manage the stack manually
luthien up
luthien logs -f
luthien down
```
````

Update the Configuration table row for `local.repo_path` (line 68) — change description to: "Auto-set by `luthien onboard`. Override to use a custom repo checkout."

- [ ] **Step 2: Run full test suite**

Run: `cd src/luthien_cli && uv run pytest -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add src/luthien_cli/README.md
git commit -m "docs(cli): update README for zero-setup onboard flow"
```

---

### Task 5: Update root CLAUDE.md CLI entry and final checks

**Files:**
- Modify: `CLAUDE.md` (root project CLAUDE.md, not the CLI's README)

- [ ] **Step 1: Update CLAUDE.md CLI entry**

The root `CLAUDE.md` has a Project Structure section with a `src/luthien_cli/` entry. Update it to mention auto-download:

```markdown
- `src/luthien_cli/`: Standalone CLI (`pipx install luthien-cli`); `luthien onboard` auto-downloads proxy artifacts
  - `commands/`: Click commands — `onboard`, `claude`, `status`, `up`/`down`, `logs`, `config`
  - `repo.py`: Manages `~/.luthien/luthien-proxy/` — downloads and updates proxy artifacts from GitHub
```

- [ ] **Step 2: Run dev_checks.sh**

Run: `./scripts/dev_checks.sh`
Expected: All checks pass (formatting, lint, type check, tests)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md CLI entry for auto-fetch behavior"
```
