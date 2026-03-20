# `luthien hackathon` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-command hackathon onboarding — `luthien hackathon` forks/clones the repo, starts a gateway from source, writes a policy template, and prints a comprehensive getting-started guide.

**Architecture:** New CLI command (`hackathon.py`) orchestrates: repo acquisition via `gh fork` or `git clone`, dependency install via `uv sync`, gateway startup via subprocess from the cloned source, policy template creation, and Rich terminal output for the hackathon guide. A new `HackathonOnboardingPolicy` (extending `TextModifierPolicy` like `OnboardingPolicy`) provides a first-turn welcome with hackathon-specific content.

**Tech Stack:** Python 3.13, Click (CLI), Rich (terminal UI), PyYAML (policy config), pytest (testing)

**Spec:** `docs/superpowers/specs/2026-03-20-hackathon-command-design.md`

---

### Task 1: HackathonOnboardingPolicy

The first-turn welcome policy for hackathon participants. Structurally identical to `OnboardingPolicy` — extends `TextModifierPolicy`, gates on `is_first_turn()`, appends hackathon-specific welcome text.

**Files:**
- Create: `src/luthien_proxy/policies/hackathon_onboarding_policy.py`
- Test: `tests/unit_tests/policies/test_hackathon_onboarding_policy.py`
- Reference: `src/luthien_proxy/policies/onboarding_policy.py` (follow this pattern exactly)
- Reference: `tests/unit_tests/policies/test_onboarding_policy.py` (follow this test pattern)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit_tests/policies/test_hackathon_onboarding_policy.py`. Follow the same structure as `test_onboarding_policy.py`:

```python
"""Unit tests for HackathonOnboardingPolicy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.policies.hackathon_onboarding_policy import (
    HackathonOnboardingPolicy,
    HackathonOnboardingPolicyConfig,
)
from luthien_proxy.policies.onboarding_policy import is_first_turn
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
    TextModifierPolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    return HackathonOnboardingPolicy({"gateway_url": "http://localhost:9999"})


@pytest.fixture
def context():
    return PolicyContext.for_testing()


class TestProtocol:
    def test_inherits_text_modifier(self, policy):
        assert isinstance(policy, TextModifierPolicy)

    def test_inherits_base_policy(self, policy):
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_interface(self, policy):
        assert isinstance(policy, AnthropicExecutionInterface)


class TestConfig:
    def test_default_gateway_url(self):
        policy = HackathonOnboardingPolicy()
        assert policy._gateway_url == "http://localhost:8000"

    def test_custom_gateway_url(self, policy):
        assert policy._gateway_url == "http://localhost:9999"

    def test_trailing_slash_stripped(self):
        policy = HackathonOnboardingPolicy({"gateway_url": "http://localhost:8000/"})
        assert policy._gateway_url == "http://localhost:8000"

    def test_config_from_pydantic(self):
        config = HackathonOnboardingPolicyConfig(gateway_url="http://example.com")
        policy = HackathonOnboardingPolicy(config)
        assert policy._gateway_url == "http://example.com"


class TestWelcomeMessage:
    def test_contains_hackathon_context(self, policy):
        welcome = policy.extra_text()
        assert "hackathon" in welcome.lower() or "Hackathon" in welcome

    def test_contains_policy_config_url(self, policy):
        welcome = policy.extra_text()
        assert "http://localhost:9999/policy-config" in welcome

    def test_contains_project_ideas(self, policy):
        welcome = policy.extra_text()
        assert "project" in welcome.lower() or "idea" in welcome.lower()

    def test_contains_key_files(self, policy):
        welcome = policy.extra_text()
        assert "policies/" in welcome

    def test_extra_text_returns_welcome(self, policy):
        assert policy.extra_text() == policy._welcome


class TestNonStreamingResponse:
    @pytest.mark.asyncio
    async def test_first_turn_appends_welcome(self, policy, context):
        context.request = {"messages": [{"role": "user", "content": "hi"}]}
        response = {
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        content_blocks = result["content"]
        assert len(content_blocks) == 2
        assert content_blocks[0]["text"] == "Hello!"
        assert "hackathon" in content_blocks[1]["text"].lower() or "Hackathon" in content_blocks[1]["text"]

    @pytest.mark.asyncio
    async def test_subsequent_turn_passthrough(self, policy, context):
        context.request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"},
            ]
        }
        response = {
            "content": [{"type": "text", "text": "I'm fine!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == "I'm fine!"


class TestRunAnthropic:
    @pytest.mark.asyncio
    async def test_passthrough_on_subsequent_turn(self, policy):
        io = MagicMock()
        io.request = {
            "stream": False,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "more"},
            ],
        }
        io.complete = AsyncMock(
            return_value={
                "content": [{"type": "text", "text": "response"}],
                "model": "test",
                "role": "assistant",
            }
        )
        context = PolicyContext.for_testing()

        results = []
        async for emission in policy.run_anthropic(io, context):
            results.append(emission)

        assert len(results) == 1
        assert results[0]["content"][0]["text"] == "response"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_hackathon_onboarding_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'luthien_proxy.policies.hackathon_onboarding_policy'`

- [ ] **Step 3: Implement HackathonOnboardingPolicy**

Create `src/luthien_proxy/policies/hackathon_onboarding_policy.py`. Follow `onboarding_policy.py` exactly — same structure, different welcome content:

```python
"""HackathonOnboardingPolicy - Welcome message for hackathon participants.

Appends a hackathon-specific welcome with project ideas, key files, and
dev workflow to the first response in a conversation. After the first turn,
the policy is completely inert.

Example config:
    policy:
      class: "luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy"
      config:
        gateway_url: "http://localhost:8000"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from luthien_proxy.policies.onboarding_policy import is_first_turn
from luthien_proxy.policy_core import TextModifierPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from anthropic.lib.streaming import MessageStreamEvent

    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core import (
        AnthropicPolicyEmission,
        AnthropicPolicyIOProtocol,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


HACKATHON_WELCOME = """

---

**Welcome to the AI Control Hackathon!** Your Luthien proxy is running and intercepting API traffic.

**What just happened:** This message was appended by the *hackathon onboarding policy*. \
It only fires on the first turn — everything after this passes through unmodified \
(unless you activate a different policy).

**Your dev workflow:**
1. Edit a policy in `src/luthien_proxy/policies/`
2. Hot-reload: `curl -X POST {gateway_url}/api/admin/policy/set -H "Authorization: Bearer $ADMIN_API_KEY" -d '{{"policy_class_ref": "your.policy:Class"}}'`
3. Or just edit `config/policy_config.yaml` and restart the gateway

**Start here:**
- `src/luthien_proxy/policies/hackathon_policy_template.py` — YOUR starter policy
- `src/luthien_proxy/policies/all_caps_policy.py` — simplest example (27 lines)
- `src/luthien_proxy/policy_core/text_modifier_policy.py` — easiest base class
- `ARCHITECTURE.md` — how the whole system works

**Project ideas:**
1. **Resampling** — if a judge rejects, resample instead of blocking
2. **Trusted model reroute** — route flagged tool calls to a trusted model
3. **Proxy commands** — `/luthien:` prefixes trigger proxy-side scripts
4. **Live policy editor** — `^^^describe changes^^^` inline while coding
5. **Character injection** — pirate/Shakespeare personas that maintain code quality

**Configure policies visually:** [{gateway_url}/policy-config]({gateway_url}/policy-config)
**Watch live traffic:** [{gateway_url}/activity/monitor]({gateway_url}/activity/monitor)
**More ideas:** https://luthienresearch.github.io/luthien-pbc-site/hackathon/

---"""


class HackathonOnboardingPolicyConfig(BaseModel):
    """Configuration for HackathonOnboardingPolicy."""

    gateway_url: str = Field(default="http://localhost:8000", description="Gateway URL for UI links")


class HackathonOnboardingPolicy(TextModifierPolicy):
    """Appends a hackathon welcome to the first response in a conversation.

    On subsequent turns, passes everything through unchanged.
    """

    def __init__(self, config: HackathonOnboardingPolicyConfig | dict | None = None):
        self.config = self._init_config(config, HackathonOnboardingPolicyConfig)
        self._gateway_url = self.config.gateway_url.rstrip("/")
        self._welcome = HACKATHON_WELCOME.format(gateway_url=self._gateway_url)

    def extra_text(self) -> str | None:
        return self._welcome

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        if is_first_turn(io.request):
            return super().run_anthropic(io, context)
        return self._passthrough(io)

    async def _passthrough(self, io: AnthropicPolicyIOProtocol) -> AsyncGenerator[AnthropicPolicyEmission, None]:
        request = io.request
        if request.get("stream", False):
            async for event in io.stream(request):
                yield event
        else:
            yield await io.complete(request)

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_response(response, context)
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_stream_event(event, context)
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_stream_complete(context)
        return []


__all__ = ["HackathonOnboardingPolicy", "HackathonOnboardingPolicyConfig"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_hackathon_onboarding_policy.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/policies/hackathon_onboarding_policy.py tests/unit_tests/policies/test_hackathon_onboarding_policy.py
git commit -m "feat: add HackathonOnboardingPolicy with first-turn welcome"
```

---

### Task 2: Hackathon Policy Template

The starter policy file written into participants' cloned repos. This is a `SimplePolicy` subclass skeleton with helpful comments and examples.

**Files:**
- Create: `src/luthien_proxy/policies/hackathon_policy_template.py`
- Test: `tests/unit_tests/policies/test_hackathon_policy_template.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit_tests/policies/test_hackathon_policy_template.py`:

```python
"""Unit tests for HackathonPolicy template — verify it imports and passes through cleanly."""

from __future__ import annotations

import pytest

from luthien_proxy.policies.hackathon_policy_template import HackathonPolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    return HackathonPolicy()


@pytest.fixture
def context():
    return PolicyContext.for_testing()


class TestTemplate:
    def test_inherits_simple_policy(self, policy):
        assert isinstance(policy, SimplePolicy)

    @pytest.mark.asyncio
    async def test_request_passthrough(self, policy, context):
        result = await policy.simple_on_request("hello world", context)
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_response_passthrough(self, policy, context):
        result = await policy.simple_on_response_content("response text", context)
        assert result == "response text"

    @pytest.mark.asyncio
    async def test_tool_call_passthrough(self, policy, context):
        tool_call = {
            "type": "tool_use",
            "id": "test-id",
            "name": "bash",
            "input": {"command": "ls"},
        }
        result = await policy.simple_on_anthropic_tool_call(tool_call, context)
        assert result == tool_call
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_hackathon_policy_template.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the template**

Create `src/luthien_proxy/policies/hackathon_policy_template.py`:

```python
"""My Hackathon Policy — [describe what it does here].

To activate via admin API (no restart needed):
    curl -X POST http://localhost:8000/api/admin/policy/set \
      -H "Authorization: Bearer $ADMIN_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"policy_class_ref": "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"}'

Or update config/policy_config.yaml and restart the gateway:
    policy:
      class: "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"
      config: {}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from luthien_proxy.policies.simple_policy import SimplePolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicToolUseBlock
    from luthien_proxy.policy_core.policy_context import PolicyContext


class HackathonPolicy(SimplePolicy):
    """My hackathon policy.

    SimplePolicy buffers streaming content so you work with complete strings.
    Override any combination of these three methods:
    - simple_on_request: modify what the user sends to the LLM
    - simple_on_response_content: modify what the LLM sends back
    - simple_on_anthropic_tool_call: inspect/modify tool calls (file writes, shell commands, etc)

    For simpler text-only transforms, consider TextModifierPolicy instead
    (see all_caps_policy.py for a 27-line example).
    """

    async def simple_on_request(self, request_str: str, context: PolicyContext) -> str:
        """Transform the user's message before it reaches the LLM.

        Examples:
            return request_str + "\\n\\nAlways respond in haiku form."
            return request_str.replace("pip install", "uv pip install")
        """
        return request_str

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform the LLM's text response before the user sees it.

        Examples:
            return content.upper()
            return content + "\\n\\n[Processed by HackathonPolicy]"
        """
        return content

    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        """Inspect or modify tool calls (file writes, shell commands, etc).

        tool_call is a dict with keys: type, id, name, input
        Common tool names: "bash", "write", "edit", "read"

        Examples:
            if tool_call["name"] == "bash":
                cmd = tool_call["input"].get("command", "")
                if "rm -rf" in cmd:
                    tool_call["input"]["command"] = "echo 'Nice try!'"
        """
        return tool_call


__all__ = ["HackathonPolicy"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_hackathon_policy_template.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/luthien_proxy/policies/hackathon_policy_template.py tests/unit_tests/policies/test_hackathon_policy_template.py
git commit -m "feat: add hackathon policy template with SimplePolicy skeleton"
```

---

### Task 3: `hackathon.py` CLI Command

The main CLI command that orchestrates the entire hackathon onboarding flow.

**Files:**
- Create: `src/luthien_cli/src/luthien_cli/commands/hackathon.py`
- Modify: `src/luthien_cli/src/luthien_cli/main.py` (add `cli.add_command(hackathon)`)
- Reference: `src/luthien_cli/src/luthien_cli/commands/onboard.py` (for gateway startup patterns)
- Reference: `src/luthien_cli/src/luthien_cli/local_process.py` (for `find_free_port`, `stop_gateway`, `start_gateway`)

**Important implementation notes:**
- `start_gateway()` in `local_process.py` hardcodes `_venv_python()` which points to `~/.luthien/venv/bin/python`. For hackathon (running from cloned source), we need to start the gateway differently — use `subprocess.Popen` with `uv run python -m luthien_proxy.main` from the cloned repo directory.
- The policy picker uses numbered `click.prompt` choices (simplest cross-platform approach).
- Fork fallback: try `gh repo fork` first, fall back to `git clone` with HTTPS URL.

- [ ] **Step 1: Create `hackathon.py`**

Create `src/luthien_cli/src/luthien_cli/commands/hackathon.py`:

```python
"""luthien hackathon -- one-command setup for hackathon participants."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel

from luthien_cli.commands.up import wait_for_healthy
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.local_process import find_free_port, is_gateway_running, stop_gateway

DEFAULT_CLONE_PATH = Path.home() / "luthien-proxy"
GITHUB_REPO = "LuthienResearch/luthien-proxy"
GITHUB_HTTPS_URL = f"https://github.com/{GITHUB_REPO}.git"

HACKATHON_PROMPT = (
    "I just joined the AI Control Hackathon and set up Luthien proxy! "
    "It's intercepting API traffic between Claude Code and the Anthropic backend. "
    "Please give a short response - the proxy's hackathon onboarding policy will "
    "append information about the hackathon, project ideas, and how to get started."
)

POLICY_CHOICES = {
    "1": (
        "HackathonOnboardingPolicy",
        "luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy",
        "Welcome message with hackathon context on first turn",
    ),
    "2": (
        "BlockDangerousCommandsPolicy",
        "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy",
        "Blocks rm -rf, chmod 777, etc. — practical safety demo",
    ),
    "3": (
        "NoYappingPolicy",
        "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy",
        "Removes filler, hedging, and preamble from responses",
    ),
    "4": (
        "AllCapsPolicy",
        "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
        "Converts all response text to UPPERCASE — simple visual demo",
    ),
    "5": (
        "NoOpPolicy",
        "luthien_proxy.policies.noop_policy:NoOpPolicy",
        "Clean passthrough — no modifications",
    ),
}

PID_FILE = "gateway.pid"
LOG_FILE = "gateway.log"


def _generate_key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _clone_repo(console: Console, clone_path: Path) -> bool:
    """Fork+clone or plain clone the repo. Returns True if repo is ready."""
    if clone_path.exists():
        git_dir = clone_path / ".git"
        if git_dir.exists():
            console.print(f"[dim]Repo already exists at {clone_path}, pulling latest...[/dim]")
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=clone_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print("[yellow]git pull failed (you may have local changes). Continuing with existing code.[/yellow]")
            return True
        else:
            console.print(f"[red]Directory {clone_path} exists but is not a git repo.[/red]")
            console.print("[dim]Choose a different path with --path or remove the directory.[/dim]")
            return False

    # Try gh fork first
    gh_path = shutil.which("gh")
    if gh_path:
        console.print("[blue]Forking and cloning repository...[/blue]")
        result = subprocess.run(
            ["gh", "repo", "fork", GITHUB_REPO, "--clone", "--default-branch-only",
             "--", str(clone_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("[green]Forked and cloned successfully.[/green]")
            return True
        console.print("[yellow]gh fork failed, falling back to git clone...[/yellow]")

    # Fallback to plain git clone
    console.print("[blue]Cloning repository...[/blue]")
    result = subprocess.run(
        ["git", "clone", GITHUB_HTTPS_URL, str(clone_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]git clone failed:[/red]\n{result.stderr}")
        return False
    console.print("[green]Cloned successfully.[/green]")
    return True


def _install_deps(console: Console, repo_path: Path) -> bool:
    """Run uv sync --dev in the cloned repo."""
    uv = shutil.which("uv")
    if not uv:
        console.print("[red]uv is required but not found.[/red]")
        console.print("[dim]Install from https://docs.astral.sh/uv/[/dim]")
        return False

    console.print("[blue]Installing dependencies...[/blue]")
    with console.status("Running uv sync --dev..."):
        result = subprocess.run(
            [uv, "sync", "--dev"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        console.print(f"[red]uv sync failed:[/red]\n{result.stderr}")
        return False
    console.print("[green]Dependencies installed.[/green]")
    return True


def _pick_policy(console: Console, yes: bool) -> tuple[str, str]:
    """Interactive policy picker. Returns (policy_class_ref, display_name)."""
    if yes:
        choice = POLICY_CHOICES["1"]
        return choice[1], choice[0]

    console.print("\n[bold]Choose a starter policy:[/bold]")
    for key, (name, _, desc) in POLICY_CHOICES.items():
        default_marker = " [green](default)[/green]" if key == "1" else ""
        console.print(f"  [{key}] [bold]{name}[/bold]{default_marker} — {desc}")

    answer = console.input("\n[bold]Pick [1-5, default=1]: [/bold]").strip()
    if not answer:
        answer = "1"
    if answer not in POLICY_CHOICES:
        console.print(f"[yellow]Invalid choice '{answer}', using default.[/yellow]")
        answer = "1"

    choice = POLICY_CHOICES[answer]
    return choice[1], choice[0]


def _write_env(repo_path: Path, proxy_key: str, admin_key: str, port: int) -> None:
    """Write .env for hackathon mode (SQLite, from source)."""
    db_path = str(repo_path / "luthien.db")
    policy_path = str(repo_path / "config" / "policy_config.yaml")

    env_content = (
        f"DATABASE_URL=sqlite:///{db_path}\n"
        f"PROXY_API_KEY={proxy_key}\n"
        f"ADMIN_API_KEY={admin_key}\n"
        f"POLICY_SOURCE=file\n"
        f"POLICY_CONFIG={policy_path}\n"
        f"AUTH_MODE=both\n"
        f"OTEL_ENABLED=false\n"
        f"USAGE_TELEMETRY=true\n"
        f"GATEWAY_PORT={port}\n"
    )
    env_path = repo_path / ".env"
    env_path.write_text(env_content)
    os.chmod(env_path, 0o600)


def _write_policy_config(repo_path: Path, policy_class_ref: str, gateway_url: str) -> None:
    """Write policy_config.yaml pointing at the chosen policy."""
    config_dir = repo_path / "config"
    config_dir.mkdir(exist_ok=True)

    # Only HackathonOnboardingPolicy uses gateway_url config
    needs_gateway_url = "hackathon_onboarding_policy" in policy_class_ref
    policy_config_data = {"gateway_url": gateway_url} if needs_gateway_url else {}

    policy_config = {
        "policy": {
            "class": policy_class_ref,
            "config": policy_config_data,
        }
    }
    with open(config_dir / "policy_config.yaml", "w") as f:
        yaml.safe_dump(policy_config, f, default_flow_style=False)


def _start_hackathon_gateway(
    console: Console, repo_path: Path, port: int
) -> int:
    """Start the gateway from source using uv run. Returns PID."""
    if not _is_unix():
        raise RuntimeError("Local mode requires Unix (Linux/macOS).")

    # Stop existing gateway if running
    pid_path = repo_path / PID_FILE
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, 0)
            stop_gateway(str(repo_path), console=console)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv not found")

    log_path = repo_path / LOG_FILE
    log_handle = open(log_path, "a")

    env = os.environ.copy()
    # Load .env into subprocess environment
    env_file = repo_path / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("'\"")
    env["GATEWAY_PORT"] = str(port)

    try:
        proc = subprocess.Popen(
            [uv, "run", "python", "-m", "luthien_proxy.main"],
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            env=env,
            cwd=str(repo_path),
        )
    except Exception:
        log_handle.close()
        raise

    log_handle.close()

    try:
        pid_path.write_text(str(proc.pid))
    except Exception:
        proc.terminate()
        raise

    return proc.pid


def _is_unix() -> bool:
    return sys.platform != "win32"


def _show_hackathon_guide(
    console: Console,
    gateway_url: str,
    repo_path: Path,
    policy_name: str,
    admin_key: str,
) -> None:
    """Print the hackathon getting-started guide."""

    # Cheatsheet
    console.print()
    console.print(
        Panel(
            textwrap.dedent(f"""\
                [bold]Scripts:[/bold]
                  ./scripts/start_gateway.sh          Start gateway (no Docker)
                  ./scripts/dev_checks.sh             Format + lint + typecheck + test
                  uv run pytest tests/unit_tests/ -x  Quick unit tests (stop on first failure)
                  uv run pytest tests/unit_tests/policies/test_hackathon_policy_template.py -v

                [bold]Hot-reload your policy (no restart needed):[/bold]
                  curl -X POST {gateway_url}/api/admin/policy/set \\
                    -H "Authorization: Bearer {admin_key}" \\
                    -H "Content-Type: application/json" \\
                    -d '{{"policy_class_ref": "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"}}'

                [bold]Or edit config/policy_config.yaml and restart the gateway.[/bold]"""),
            title="Cheatsheet",
            border_style="cyan",
        )
    )

    # UI Tour
    console.print(
        Panel(
            textwrap.dedent(f"""\
                {gateway_url}/policy-config        Visual policy picker and config editor
                {gateway_url}/activity/monitor     Live stream of requests and responses
                {gateway_url}/diffs                Before/after policy transformation diffs
                {gateway_url}/request-logs/viewer  Full HTTP request/response log viewer
                {gateway_url}/health               Gateway health check"""),
            title="UI Tour",
            border_style="magenta",
        )
    )

    # Key Files
    console.print(
        Panel(
            textwrap.dedent("""\
                [bold]Start here:[/bold]
                  src/luthien_proxy/policies/hackathon_policy_template.py    YOUR policy
                  src/luthien_proxy/policies/all_caps_policy.py              Simplest example (27 lines)
                  src/luthien_proxy/policy_core/text_modifier_policy.py      Easiest base class
                  config/policy_config.yaml                                  Active policy config

                [bold]Go deeper:[/bold]
                  src/luthien_proxy/policies/simple_policy.py                Medium complexity base
                  src/luthien_proxy/policies/tool_call_judge_policy.py       Advanced: LLM judge
                  ARCHITECTURE.md                                            Full system design
                  docs/policies.md                                           Policy reference"""),
            title="Key Files",
            border_style="blue",
        )
    )

    # Project Ideas
    console.print(
        Panel(
            textwrap.dedent("""\
                1. [bold]Resampling Policy[/bold] — if a judge rejects a response, resample instead of blocking
                2. [bold]Trusted Model Reroute[/bold] — route flagged tool calls to a trusted model
                3. [bold]Proxy Commands[/bold] — /luthien: prefixes in messages trigger proxy-side scripts
                4. [bold]Live Policy Editor[/bold] — ^^^describe changes^^^ inline while coding
                5. [bold]Character Injection[/bold] — pirate/anime/Shakespeare personas + code quality
                6. [bold]Model Router[/bold] — sonnet:/haiku: prefixes route to different models
                7. [bold]Self-Modifying Policy[/bold] — evolves based on conversation context
                8. [bold]Red Team[/bold] — try to extract hidden state through prompt injection"""),
            title="Project Ideas",
            border_style="yellow",
        )
    )

    # Links
    console.print(
        Panel(
            textwrap.dedent("""\
                Hackathon:   https://luthienresearch.github.io/luthien-pbc-site/hackathon/
                GitHub:      https://github.com/LuthienResearch/luthien-proxy
                Docs:        ARCHITECTURE.md in your cloned repo"""),
            title="Links",
            border_style="green",
        )
    )

    # Status
    console.print()
    console.print(
        Panel(
            textwrap.dedent(f"""\
                [green bold]Gateway is running![/green bold]

                [bold]Gateway URL:[/bold]   {gateway_url}
                [bold]Policy:[/bold]        {policy_name}
                [bold]Repo:[/bold]          {repo_path}

                [bold]Manage the gateway:[/bold]
                  luthien status     # check health
                  luthien logs       # view logs
                  luthien down       # stop the gateway
                  luthien up         # start again
                  [bold yellow]luthien claude[/bold yellow]    # launch Claude Code through the proxy"""),
            title="Ready",
            border_style="green",
        )
    )


def _read_single_key() -> str:
    """Read a single keypress without waiting for Enter (Unix/macOS only)."""
    if not sys.stdin.isatty():
        return sys.stdin.read(1) or "\n"

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


@click.command()
@click.option("--path", default=str(DEFAULT_CLONE_PATH), help="Where to clone the repo")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompts")
def hackathon(path: str, yes: bool):
    """Set up for the AI Control Hackathon — fork, clone, install, and start hacking."""
    console = Console()
    clone_path = Path(path).expanduser().resolve()

    # 1. Welcome
    console.print(
        Panel(
            textwrap.dedent("""\
                Welcome to the [bold]AI Control Hackathon[/bold]!

                This will:
                  1. Fork & clone the luthien-proxy repo
                  2. Install dependencies
                  3. Start a local gateway
                  4. Create a starter policy template for you
                  5. Show you everything you need to start hacking

                [dim]No Docker required. Uses SQLite for storage.[/dim]"""),
            title="Luthien Hackathon",
            border_style="yellow",
        )
    )

    if not yes:
        try:
            answer = console.input(f"[bold]Clone to {clone_path}? [Y/n]: [/bold]")
            if answer.strip().lower() in ("n", "no"):
                console.print("[dim]Cancelled.[/dim]")
                return
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled.[/dim]")
            return

    # 2. Clone
    if not _clone_repo(console, clone_path):
        raise SystemExit(1)

    # 3. Install deps
    if not _install_deps(console, clone_path):
        raise SystemExit(1)

    # 4. Pick policy
    policy_class_ref, policy_name = _pick_policy(console, yes)

    # 5. Generate keys and configure
    proxy_key = _generate_key("sk-luthien")
    admin_key = _generate_key("admin")
    gateway_port = find_free_port(8000)
    gateway_url = f"http://localhost:{gateway_port}"

    console.print("\n[blue]Configuring gateway...[/blue]")
    _write_env(clone_path, proxy_key, admin_key, gateway_port)
    _write_policy_config(clone_path, policy_class_ref, gateway_url)

    # 6. Start gateway
    console.print(f"[blue]Starting gateway on port {gateway_port}...[/blue]")
    pid = _start_hackathon_gateway(console, clone_path, gateway_port)
    console.print(f"[dim]Gateway started (PID {pid})[/dim]")

    # 7. Save CLI config
    config = load_config(DEFAULT_CONFIG_PATH)
    config.gateway_url = gateway_url
    config.api_key = proxy_key
    config.admin_key = admin_key
    config.mode = "local"
    config.repo_path = str(clone_path)
    save_config(config, DEFAULT_CONFIG_PATH)

    # 8. Wait for healthy
    if not wait_for_healthy(gateway_url, console=console):
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        console.print("[dim]Check logs: luthien logs[/dim]")
        raise SystemExit(1)

    # 9. Show guide
    _show_hackathon_guide(console, gateway_url, clone_path, policy_name, admin_key)

    # 10. Launch Claude Code
    console.print("[bold]Press any key to launch Claude Code through the proxy, or q to quit.[/bold]")
    try:
        key = _read_single_key()
        if key.lower() == "q":
            return
    except (KeyboardInterrupt, EOFError):
        return

    from luthien_cli.commands.claude import _launch_claude

    _launch_claude(console, [HACKATHON_PROMPT])
```

- [ ] **Step 2: Register the command in `main.py`**

In `src/luthien_cli/src/luthien_cli/main.py`, add the hackathon import and registration alongside the existing commands:

```python
# Add to the import block (after line 18):
from luthien_cli.commands.hackathon import hackathon

# Add to the registration block (after line 27):
cli.add_command(hackathon)
```

- [ ] **Step 3: Run the existing CLI tests to verify nothing is broken**

Run: `cd src/luthien_cli && uv run pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/luthien_cli/src/luthien_cli/commands/hackathon.py src/luthien_cli/src/luthien_cli/main.py
git commit -m "feat: add 'luthien hackathon' CLI command for hackathon onboarding"
```

---

### Task 4: Verify and Polish

Run full checks, fix any issues, make a final commit.

**Files:**
- All files from Tasks 1-3

- [ ] **Step 1: Run dev_checks on the proxy**

Run: `./scripts/dev_checks.sh`
Expected: All formatting, linting, type checking, and tests pass

- [ ] **Step 2: Fix any issues found by dev_checks**

Address any ruff, pyright, or test failures. Common issues:
- Import ordering (ruff will auto-fix)
- Type annotations pyright flags
- Line length

- [ ] **Step 3: Run CLI tests**

Run: `cd src/luthien_cli && uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Verify the hackathon policy template imports**

Run: `uv run python -c "from luthien_proxy.policies.hackathon_policy_template import HackathonPolicy; print('OK')"`
Expected: Prints `OK`

Run: `uv run python -c "from luthien_proxy.policies.hackathon_onboarding_policy import HackathonOnboardingPolicy; print('OK')"`
Expected: Prints `OK`

- [ ] **Step 5: Final commit if fixes were needed**

```bash
git add -A
git commit -m "fix: address dev_checks issues in hackathon command"
```

- [ ] **Step 6: Push and open draft PR**

```bash
git push -u origin $(git branch --show-current)
gh pr create --draft --title "feat: luthien hackathon command for hackathon onboarding" --body "$(cat <<'EOF'
## Summary
- Adds `luthien hackathon` CLI command for one-command hackathon onboarding
- Forks/clones the repo, installs deps, starts gateway from source
- Interactive policy picker with 5 starter options
- HackathonOnboardingPolicy: first-turn welcome with project ideas and key files
- Hackathon policy template: SimplePolicy skeleton ready to customize
- Rich terminal guide: cheatsheet, UI tour, key files, project ideas, links

## Test plan
- [ ] `uv run pytest tests/unit_tests/policies/test_hackathon_onboarding_policy.py -v`
- [ ] `uv run pytest tests/unit_tests/policies/test_hackathon_policy_template.py -v`
- [ ] `./scripts/dev_checks.sh` passes
- [ ] Manual: `luthien hackathon --path /tmp/test-hackathon -y` completes successfully
- [ ] Manual: Gateway starts and responds to /health
- [ ] Manual: Claude Code launches through the proxy

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
