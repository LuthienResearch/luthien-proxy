# `--proxy-ref` CLI Option Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--proxy-ref` option to `onboard`, `up`, and `hackathon` commands so users can run the proxy from a specific commit, branch, or PR.

**Architecture:** A `resolve_proxy_ref()` function in `repo.py` handles `#N` → PR head branch resolution via GitHub API. Plain strings (branch names, tags, commit SHAs) pass through unchanged — git natively resolves them in order (branch → tag → SHA), so no explicit fallback logic is needed. The resolved ref is passed through to `ensure_gateway_venv()` (which appends `@<ref>` to the pip git URL) or to hackathon's clone flow (which does `git fetch` + `git checkout`). Docker mode rejects `--proxy-ref` with a clear error.

**Tech Stack:** Python, Click, httpx, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/luthien_cli/src/luthien_cli/repo.py` | Modify | Add `resolve_proxy_ref()` and update `ensure_gateway_venv()` signature |
| `src/luthien_cli/src/luthien_cli/commands/onboard.py` | Modify | Add `--proxy-ref` option, pass to venv flow, reject with `--docker` |
| `src/luthien_cli/src/luthien_cli/commands/up.py` | Modify | Add `--proxy-ref` option, pass to venv flow, reject if docker mode |
| `src/luthien_cli/src/luthien_cli/commands/hackathon.py` | Modify | Add `--proxy-ref` option, checkout ref after clone |
| `src/luthien_cli/tests/test_repo.py` | Modify | Tests for `resolve_proxy_ref()` and `ensure_gateway_venv(proxy_ref=...)` |
| `src/luthien_cli/tests/test_onboard.py` | Modify | Tests for `--proxy-ref` on onboard |
| `src/luthien_cli/tests/test_up.py` | Modify | Tests for `--proxy-ref` on up |
| `src/luthien_cli/tests/test_hackathon.py` | Modify | Tests for `--proxy-ref` on hackathon |

---

### Task 1: `resolve_proxy_ref()` in `repo.py`

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/repo.py`
- Test: `src/luthien_cli/tests/test_repo.py`

- [ ] **Step 1: Write failing tests for `resolve_proxy_ref()`**

Add to `test_repo.py`:

```python
from luthien_cli.repo import resolve_proxy_ref


def test_resolve_proxy_ref_plain_branch():
    """Plain ref passes through unchanged."""
    assert resolve_proxy_ref("feature/foo") == "feature/foo"


def test_resolve_proxy_ref_commit_sha():
    """Commit SHA passes through unchanged."""
    assert resolve_proxy_ref("abc1234def") == "abc1234def"


def test_resolve_proxy_ref_pr_number(httpx_mock):
    """PR ref #123 resolves to the PR's head branch."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/123",
        json={"head": {"ref": "feature/cool-thing"}},
    )
    assert resolve_proxy_ref("#123") == "feature/cool-thing"


def test_resolve_proxy_ref_pr_not_found(httpx_mock):
    """PR ref for non-existent PR raises SystemExit."""
    httpx_mock.add_response(
        url="https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/999",
        status_code=404,
    )
    with pytest.raises(SystemExit):
        resolve_proxy_ref("#999")


def test_resolve_proxy_ref_pr_network_error(httpx_mock):
    """Network error resolving PR raises SystemExit."""
    httpx_mock.add_exception(
        httpx.ConnectError("offline"),
        url="https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/42",
    )
    with pytest.raises(SystemExit):
        resolve_proxy_ref("#42")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_repo.py -k "resolve_proxy_ref" -v`
Expected: ImportError — `resolve_proxy_ref` doesn't exist yet

- [ ] **Step 3: Implement `resolve_proxy_ref()`**

Add to `repo.py` after the existing constants:

```python
GITHUB_PR_URL = "https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/{number}"


def resolve_proxy_ref(ref: str) -> str:
    """Resolve a proxy ref string to a git ref.

    Plain strings (branches, tags, SHAs) pass through.
    '#N' resolves PR N's head branch via GitHub API.
    """
    if not ref.startswith("#"):
        return ref

    pr_number = ref[1:]
    console = Console(stderr=True)
    url = GITHUB_PR_URL.format(number=pr_number)
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Could not find PR #{pr_number}: HTTP {e.response.status_code}[/red]")
        raise SystemExit(1)
    except httpx.HTTPError as e:
        console.print(f"[red]Could not resolve PR #{pr_number}: {e!r}[/red]")
        raise SystemExit(1)

    branch = r.json()["head"]["ref"]
    console.print(f"[blue]PR #{pr_number} → branch {branch}[/blue]")
    return branch
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_repo.py -k "resolve_proxy_ref" -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/repo.py src/luthien_cli/tests/test_repo.py
git commit -m "feat(cli): add resolve_proxy_ref() for PR/branch/SHA resolution"
```

---

### Task 2: Update `ensure_gateway_venv()` to accept `proxy_ref`

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/repo.py`
- Test: `src/luthien_cli/tests/test_repo.py`

- [ ] **Step 1: Write failing tests**

Add to `test_repo.py`:

```python
def test_ensure_gateway_venv_with_proxy_ref(tmp_path):
    """proxy_ref appends @ref to the git install URL."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv") as mock_uv,
    ):
        # Simulate venv not existing (needs_install=True)
        ensure_gateway_venv(proxy_ref="feature/cool")

    # Find the pip install call
    install_call = [c for c in mock_uv.call_args_list if c.args[0] == "pip"][0]
    install_args = list(install_call.args)
    github_url = [a for a in install_args if "github.com" in a][0]
    assert github_url.endswith("@feature/cool")


def test_ensure_gateway_venv_without_proxy_ref(tmp_path):
    """No proxy_ref uses bare git URL (no @suffix)."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv") as mock_uv,
    ):
        ensure_gateway_venv()

    install_call = [c for c in mock_uv.call_args_list if c.args[0] == "pip"][0]
    install_args = list(install_call.args)
    github_url = [a for a in install_args if "github.com" in a][0]
    assert github_url == "git+https://github.com/LuthienResearch/luthien-proxy.git"


def test_ensure_gateway_venv_with_ref_shows_message(tmp_path, capsys):
    """Using a ref prints a message about the ref being used."""
    venv_dir = tmp_path / "venv"
    repo_dir = tmp_path / "repo"

    with (
        patch("luthien_cli.repo.MANAGED_VENV_DIR", venv_dir),
        patch("luthien_cli.repo.MANAGED_REPO_DIR", repo_dir),
        patch("luthien_cli.repo._run_uv"),
    ):
        ensure_gateway_venv(proxy_ref="abc123")

    captured = capsys.readouterr()
    assert "abc123" in captured.err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_repo.py -k "ensure_gateway_venv" -v`
Expected: TypeError — `ensure_gateway_venv()` doesn't accept `proxy_ref`

- [ ] **Step 3: Update `ensure_gateway_venv()` implementation**

Modify in `repo.py`:

```python
def ensure_gateway_venv(proxy_ref: str | None = None) -> str:
    """Create a managed venv and install luthien-proxy. Returns repo path.

    Creates ~/.luthien/venv/ with luthien-proxy installed, and ensures
    the ~/.luthien/luthien-proxy/ directory exists for config/data files.
    """
    console = Console(stderr=True)
    venv_dir = MANAGED_VENV_DIR
    repo_dir = MANAGED_REPO_DIR

    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "config").mkdir(exist_ok=True)

    venv_python = venv_dir / "bin" / "python"
    needs_install = not venv_python.exists()

    if needs_install:
        console.print("[blue]Creating gateway environment...[/blue]")
        with console.status("Setting up Python environment..."):
            _run_uv("venv", str(venv_dir), "--python", "3.13", console=console)

    github_source = "git+https://github.com/LuthienResearch/luthien-proxy.git"
    if proxy_ref:
        github_source = f"{github_source}@{proxy_ref}"
        console.print(f"[blue]Using proxy ref: {proxy_ref}[/blue]")

    install_args = [
        "pip",
        "install",
        "--python",
        str(venv_python),
        github_source,
    ]
    if needs_install:
        label = "Installing luthien-proxy..."
    else:
        label = "Checking luthien-proxy..."
        install_args.append("--upgrade")

    with console.status(label):
        _run_uv(*install_args, console=console)

    console.print("[green]Gateway package installed.[/green]")
    return str(repo_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_repo.py -k "ensure_gateway_venv" -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/repo.py src/luthien_cli/tests/test_repo.py
git commit -m "feat(cli): ensure_gateway_venv accepts proxy_ref for version targeting"
```

---

### Task 3: Add `--proxy-ref` to `onboard` command

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/commands/onboard.py`
- Test: `src/luthien_cli/tests/test_onboard.py`

- [ ] **Step 1: Write failing tests**

Add to `test_onboard.py`:

```python
def test_onboard_local_with_proxy_ref(tmp_path):
    """--proxy-ref is passed through to ensure_gateway_venv."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)) as mock_venv,
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        result = runner.invoke(cli, ["onboard", "--proxy-ref", "abc123"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    mock_venv.assert_called_once_with(proxy_ref="abc123")


def test_onboard_docker_with_proxy_ref_errors(tmp_path):
    """--proxy-ref with --docker should error."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"

    with patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["onboard", "--docker", "--proxy-ref", "abc123", "-y"])

    assert result.exit_code != 0
    assert "docker" in result.output.lower()


def test_onboard_local_with_pr_ref(tmp_path):
    """--proxy-ref '#123' resolves PR before passing to ensure_gateway_venv."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.resolve_proxy_ref", return_value="feature/cool") as mock_resolve,
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)) as mock_venv,
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        result = runner.invoke(cli, ["onboard", "--proxy-ref", "#123"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    mock_resolve.assert_called_once_with("#123")
    mock_venv.assert_called_once_with(proxy_ref="feature/cool")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_onboard.py -k "proxy_ref" -v`
Expected: FAIL — option not recognized

- [ ] **Step 3: Implement `--proxy-ref` on `onboard`**

In `onboard.py`, add import:
```python
from luthien_cli.repo import ensure_gateway_venv, ensure_repo, resolve_proxy_ref
```

Update `_onboard_local` signature:
```python
def _onboard_local(console: Console, config, proxy_key: str, admin_key: str, proxy_ref: str | None = None) -> None:
```

Update the `ensure_gateway_venv` call inside `_onboard_local`:
```python
    config.repo_path = ensure_gateway_venv(proxy_ref=proxy_ref)
```

Update the click command:
```python
@click.command()
@click.option("--docker", "use_docker", is_flag=True, help="Use Docker (PostgreSQL + Redis) instead of local mode")
@click.option("--proxy-ref", default=None, help="Git ref (branch, commit, or #PR) of luthien-proxy to install")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
def onboard(use_docker: bool, proxy_ref: str | None, yes: bool):
    """Set up a local Luthien gateway and start it with the onboarding policy."""
    console = Console()

    if use_docker and proxy_ref:
        console.print("[red]--proxy-ref is not supported with --docker mode.[/red]")
        raise SystemExit(1)

    if proxy_ref:
        proxy_ref = resolve_proxy_ref(proxy_ref)

    config = load_config(DEFAULT_CONFIG_PATH)
    # ... rest unchanged ...

    if use_docker:
        _onboard_docker(console, config, proxy_key, admin_key)
    else:
        _onboard_local(console, config, proxy_key, admin_key, proxy_ref=proxy_ref)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_onboard.py -v`
Expected: All PASS (both new and existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/commands/onboard.py src/luthien_cli/tests/test_onboard.py
git commit -m "feat(cli): add --proxy-ref to onboard command"
```

---

### Task 4: Add `--proxy-ref` to `up` command

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/commands/up.py`
- Test: `src/luthien_cli/tests/test_up.py`

- [ ] **Step 1: Write failing tests**

Add to `test_up.py`:

```python
def test_up_local_with_proxy_ref(tmp_path):
    """--proxy-ref is passed to ensure_gateway_venv when bootstrapping."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n\n[local]\nmode = "local"\n')

    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.ensure_gateway_venv", return_value=str(tmp_path)) as mock_venv,
        patch("luthien_cli.commands.up.is_gateway_running", return_value=None),
        patch("luthien_cli.commands.up.start_gateway", return_value=12345),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up", "--proxy-ref", "abc123"])
        assert result.exit_code == 0
        mock_venv.assert_called_once_with(proxy_ref="abc123")


def test_up_docker_with_proxy_ref_errors(tmp_path):
    """--proxy-ref with docker mode should error."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )

    with patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["up", "--proxy-ref", "abc123"])

    assert result.exit_code != 0
    assert "docker" in result.output.lower()


def test_up_local_with_pr_ref(tmp_path):
    """--proxy-ref '#42' resolves PR before passing to venv."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n\n[local]\nmode = "local"\n')

    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.resolve_proxy_ref", return_value="feature/pr-branch") as mock_resolve,
        patch("luthien_cli.commands.up.ensure_gateway_venv", return_value=str(tmp_path)) as mock_venv,
        patch("luthien_cli.commands.up.is_gateway_running", return_value=None),
        patch("luthien_cli.commands.up.start_gateway", return_value=12345),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up", "--proxy-ref", "#42"])
        assert result.exit_code == 0
        mock_resolve.assert_called_once_with("#42")
        mock_venv.assert_called_once_with(proxy_ref="feature/pr-branch")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_up.py -k "proxy_ref" -v`
Expected: FAIL — option not recognized

- [ ] **Step 3: Implement `--proxy-ref` on `up`**

In `up.py`, add import:
```python
from luthien_cli.repo import ensure_gateway_venv, ensure_repo, resolve_proxy_ref
```

Update `ensure_gateway_up` to accept `proxy_ref`:
```python
def ensure_gateway_up(console: Console, proxy_ref: str | None = None) -> None:
```

In the local mode branch of `ensure_gateway_up`, pass `proxy_ref` to `ensure_gateway_venv`:
```python
        if not config.repo_path:
            config.repo_path = ensure_gateway_venv(proxy_ref=proxy_ref)
```

Update the click command:
```python
@click.command()
@click.option("--follow", "-f", is_flag=True, help="Tail gateway logs after startup")
@click.option("--proxy-ref", default=None, help="Git ref (branch, commit, or #PR) of luthien-proxy to install")
def up(follow: bool, proxy_ref: str | None):
    """Start the gateway (auto-detects local or Docker mode)."""
    console = Console()

    if proxy_ref:
        config = load_config(DEFAULT_CONFIG_PATH)
        if config.mode == "docker":
            console.print("[red]--proxy-ref is not supported with Docker mode.[/red]")
            raise SystemExit(1)
        proxy_ref = resolve_proxy_ref(proxy_ref)

    ensure_gateway_up(console, proxy_ref=proxy_ref)
    # ... rest unchanged ...
```

Also update the `claude` command's call to `ensure_gateway_up` — it passes no `proxy_ref`, which is fine since the parameter defaults to `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_up.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/commands/up.py src/luthien_cli/tests/test_up.py
git commit -m "feat(cli): add --proxy-ref to up command"
```

---

### Task 5: Add `--proxy-ref` to `hackathon` command

**Files:**
- Modify: `src/luthien_cli/src/luthien_cli/commands/hackathon.py`
- Test: `src/luthien_cli/tests/test_hackathon.py`

- [ ] **Step 1: Write failing tests**

Add to `test_hackathon.py`:

```python
from luthien_cli.commands.hackathon import _checkout_proxy_ref


class TestCheckoutProxyRef:
    """Tests for _checkout_proxy_ref()."""

    def test_checkout_branch(self, tmp_path):
        """Plain branch ref does git checkout."""
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _checkout_proxy_ref(console, tmp_path, "feature/foo")

        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd == ["git", "checkout", "feature/foo"]

    def test_checkout_pr(self, tmp_path):
        """PR number fetches the PR ref and checks it out."""
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = _checkout_proxy_ref(console, tmp_path, "pr-123", pr_number=123)

        assert result is True
        assert mock_run.call_count == 2
        fetch_cmd = mock_run.call_args_list[0].args[0]
        assert "pull/123/head:pr-123" in " ".join(fetch_cmd)

    def test_checkout_failure(self, tmp_path):
        """Failed checkout returns False."""
        console = Console(file=io.StringIO())
        with patch("luthien_cli.commands.hackathon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            result = _checkout_proxy_ref(console, tmp_path, "nonexistent")

        assert result is False


class TestHackathonProxyRef:
    """Tests for --proxy-ref on hackathon command."""

    def test_hackathon_help_shows_proxy_ref(self):
        runner = CliRunner()
        result = runner.invoke(hackathon, ["--help"])
        assert "--proxy-ref" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/luthien_cli && uv run pytest tests/test_hackathon.py -k "proxy_ref or CheckoutProxyRef" -v`
Expected: ImportError — `_checkout_proxy_ref` doesn't exist

- [ ] **Step 3: Implement `--proxy-ref` on `hackathon`**

Add `_checkout_proxy_ref()` to `hackathon.py`:

```python
def _checkout_proxy_ref(console: Console, repo_path: Path, ref: str, pr_number: int | None = None) -> bool:
    """Checkout a specific ref in the cloned repo.

    For PRs, fetches the PR head first. For branches/SHAs, does a plain checkout.
    """
    if pr_number is not None:
        console.print(f"[blue]Fetching PR #{pr_number}...[/blue]")
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", f"pull/{pr_number}/head:{ref}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            console.print(f"[red]Failed to fetch PR #{pr_number}:[/red]\n{fetch_result.stderr}")
            return False

    result = subprocess.run(
        ["git", "checkout", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Failed to checkout {ref}:[/red]\n{result.stderr}")
        return False

    console.print(f"[green]Checked out {ref}[/green]")
    return True
```

Add import and update the click command:
```python
from luthien_cli.repo import resolve_proxy_ref

@click.command()
@click.option("--path", default=str(DEFAULT_CLONE_PATH), help="Where to clone the repo")
@click.option("--proxy-ref", default=None, help="Git ref (branch, commit, or #PR) of luthien-proxy to use")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompts")
def hackathon(path: str, proxy_ref: str | None, yes: bool) -> None:
```

After `_clone_repo` succeeds and before `_install_deps`, add:
```python
    # 1.5 Checkout specific ref if requested
    pr_number = None
    if proxy_ref:
        if proxy_ref.startswith("#"):
            pr_number = int(proxy_ref[1:])
            ref_branch = f"pr-{pr_number}"
        else:
            ref_branch = proxy_ref
        if not _checkout_proxy_ref(console, clone_path, ref_branch, pr_number=pr_number):
            raise SystemExit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/luthien_cli && uv run pytest tests/test_hackathon.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/commands/hackathon.py src/luthien_cli/tests/test_hackathon.py
git commit -m "feat(cli): add --proxy-ref to hackathon command"
```

---

### Task 6: Full integration pass

- [ ] **Step 1: Run all CLI tests**

Run: `cd src/luthien_cli && uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run linting and formatting**

Run: `cd src/luthien_cli && uv run ruff format . && uv run ruff check --fix .`
Expected: Clean

- [ ] **Step 3: Verify help text for all three commands**

Run:
```bash
cd src/luthien_cli && uv run luthien onboard --help
cd src/luthien_cli && uv run luthien up --help
cd src/luthien_cli && uv run luthien hackathon --help
```
Expected: All show `--proxy-ref` in help output

- [ ] **Step 4: Commit any formatting fixes**

```bash
git add -u
git commit -m "style: format proxy-ref changes"
```
