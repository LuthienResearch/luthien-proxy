# Overseer E2E Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a multi-turn e2e test harness that runs open-ended Claude Code sessions in a Docker sandbox, routed through the proxy, with an overseer LLM monitoring for proxy bugs and a live HTML dashboard.

**Architecture:** Docker sandbox container (node + claude CLI) runs `claude -p` sessions. A Python overseer script on the host drives the session turn-by-turn via `docker exec`, using `--resume` to continue sessions. An overseer LLM (Haiku, direct API) analyzes each turn's output and generates the next prompt. A tiny aiohttp server pushes live updates to an HTML dashboard via SSE.

**Tech Stack:** Python 3.13, aiohttp, anthropic SDK, Docker, Claude Code CLI (`claude -p --output-format stream-json`)

**Design doc:** `docs/plans/2026-03-03-overseer-e2e-design.md`

---

### Task 1: Docker Sandbox Image

**Files:**
- Create: `docker/sandbox/Dockerfile`
- Modify: `docker-compose.yaml` (add sandbox service)

**Step 1: Write the Dockerfile**

```dockerfile
# Sandbox for overseer e2e tests — runs Claude Code sessions in isolation
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git python3 python3-venv build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox
WORKDIR /work

# Container stays alive; overseer does `docker exec` into it
ENTRYPOINT ["sleep", "infinity"]
```

**Step 2: Add sandbox service to docker-compose.yaml**

Add after the `gateway` service:

```yaml
  # Sandbox for overseer e2e tests (Claude Code sessions in isolation)
  sandbox:
    build:
      context: .
      dockerfile: docker/sandbox/Dockerfile
    profiles: ["overseer"]
    environment:
      - ANTHROPIC_BASE_URL=http://gateway:8000
      - ANTHROPIC_API_KEY=${PROXY_API_KEY}
    depends_on:
      gateway:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 1G
```

The `profiles: ["overseer"]` means it only starts when explicitly requested (`docker compose --profile overseer up sandbox`).

**Step 3: Build and test the image**

Run:
```bash
docker compose --profile overseer build sandbox
docker compose --profile overseer up -d sandbox
docker compose exec sandbox claude --version
docker compose exec sandbox claude --help | head -5
```

Expected: Claude CLI version prints, help output shows.

**Step 4: Commit**

```bash
git add docker/sandbox/Dockerfile docker-compose.yaml
git commit -m "feat: add Docker sandbox for overseer e2e tests"
```

---

### Task 2: Stream-JSON Parser Module

Extract and extend the existing `parse_stream_json` and event classes from `tests/e2e_tests/test_claude_code.py` into a reusable module.

**Files:**
- Create: `scripts/overseer/stream_parser.py`
- Create: `scripts/overseer/__init__.py`
- Test: `tests/unit_tests/test_overseer_stream_parser.py`

**Step 1: Write the failing tests**

```python
import json
from scripts.overseer.stream_parser import parse_stream_json, TurnSummary, summarize_turn

def test_parse_init_event():
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc-123", "model": "claude-sonnet-4-6"})
    events = parse_stream_json(line)
    assert len(events) == 1
    assert events[0].type == "system"
    assert events[0].subtype == "init"
    assert events[0].session_id == "abc-123"

def test_parse_assistant_with_tool_use():
    line = json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Let me read that file."},
                {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "/tmp/test.txt"}},
            ]
        }
    })
    events = parse_stream_json(line)
    assert len(events) == 1
    tool_uses = events[0].get_tool_uses()
    assert len(tool_uses) == 1
    assert tool_uses[0]["name"] == "Read"

def test_parse_result_event():
    line = json.dumps({
        "type": "result",
        "subtype": "success",
        "result": "Done!",
        "session_id": "abc-123",
        "is_error": False,
        "num_turns": 3,
        "total_cost_usd": 0.05,
    })
    events = parse_stream_json(line)
    assert events[0].is_result
    assert events[0].is_success

def test_parse_malformed_line_skipped():
    output = "not json\n" + json.dumps({"type": "system", "subtype": "init", "session_id": "x"})
    events = parse_stream_json(output)
    assert len(events) == 1

def test_summarize_turn():
    raw = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet-4-6"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll read the file."},
            {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "/tmp/a.txt"}},
        ]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "hello"},
        ]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "The file says hello.",
                     "session_id": "s1", "is_error": False, "num_turns": 1, "total_cost_usd": 0.02}),
    ])
    summary = summarize_turn(raw, turn_number=1, start_time=0.0, end_time=2.5)
    assert summary.session_id == "s1"
    assert summary.turn_number == 1
    assert "Read" in summary.tools_used
    assert summary.cost_usd == 0.02
    assert summary.duration_seconds == 2.5
    assert summary.is_success
    assert len(summary.anomalies) == 0

def test_summarize_turn_detects_error():
    raw = json.dumps({"type": "result", "subtype": "error", "result": "API error",
                       "session_id": "s1", "is_error": True, "num_turns": 0, "total_cost_usd": 0.0})
    summary = summarize_turn(raw, turn_number=1, start_time=0.0, end_time=1.0)
    assert not summary.is_success
    assert any("error" in a.lower() for a in summary.anomalies)

def test_summarize_turn_detects_slow_turn():
    raw = json.dumps({"type": "result", "subtype": "success", "result": "ok",
                       "session_id": "s1", "is_error": False, "num_turns": 1, "total_cost_usd": 0.01})
    summary = summarize_turn(raw, turn_number=1, start_time=0.0, end_time=120.0, slow_threshold=60.0)
    assert any("slow" in a.lower() for a in summary.anomalies)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/test_overseer_stream_parser.py -v`
Expected: ImportError — module doesn't exist yet.

**Step 3: Implement the stream parser**

Create `scripts/overseer/__init__.py` (empty).

Create `scripts/overseer/stream_parser.py`:

```python
"""Parse Claude Code stream-json output and summarize turns for anomaly detection."""

import json
from dataclasses import dataclass, field


@dataclass
class StreamEvent:
    """A parsed event from claude stream-json output."""

    type: str
    subtype: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return self.raw.get("session_id", "")

    @property
    def is_result(self) -> bool:
        return self.type == "result"

    @property
    def is_success(self) -> bool:
        return self.is_result and self.subtype == "success"

    def get_tool_uses(self) -> list[dict]:
        if self.type != "assistant":
            return []
        content = self.raw.get("message", {}).get("content", [])
        return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]

    def get_tool_results(self) -> list[dict]:
        if self.type != "user":
            return []
        content = self.raw.get("message", {}).get("content", [])
        return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]

    def get_text(self) -> str:
        if self.type != "assistant":
            return ""
        content = self.raw.get("message", {}).get("content", [])
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


@dataclass
class TurnSummary:
    """Summary of a single overseer turn for reporting and anomaly detection."""

    turn_number: int
    session_id: str
    is_success: bool
    tools_used: list[str]
    tool_call_count: int
    tool_result_count: int
    cost_usd: float
    duration_seconds: float
    result_text: str
    anomalies: list[str]
    num_turns_reported: int  # num_turns from Claude Code's result event


def parse_stream_json(output: str) -> list[StreamEvent]:
    """Parse newline-delimited JSON stream output into events."""
    events = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            events.append(StreamEvent(
                type=data.get("type", "unknown"),
                subtype=data.get("subtype"),
                raw=data,
            ))
        except json.JSONDecodeError:
            continue
    return events


def summarize_turn(
    raw_output: str,
    turn_number: int,
    start_time: float,
    end_time: float,
    slow_threshold: float = 60.0,
) -> TurnSummary:
    """Summarize a turn's stream-json output with rule-based anomaly detection."""
    events = parse_stream_json(raw_output)
    anomalies: list[str] = []

    session_id = ""
    is_success = False
    result_text = ""
    cost_usd = 0.0
    num_turns_reported = 0
    tools_used: list[str] = []
    tool_call_count = 0
    tool_result_count = 0

    for event in events:
        if event.session_id:
            session_id = event.session_id

        for tu in event.get_tool_uses():
            tools_used.append(tu.get("name", "unknown"))
            tool_call_count += 1

        tool_result_count += len(event.get_tool_results())

        if event.is_result:
            is_success = event.is_success
            result_text = event.raw.get("result", "")
            cost_usd = event.raw.get("total_cost_usd", 0.0)
            num_turns_reported = event.raw.get("num_turns", 0)
            if event.raw.get("is_error"):
                anomalies.append(f"Error result: {result_text[:200]}")

    duration = end_time - start_time
    if duration > slow_threshold:
        anomalies.append(f"Slow turn: {duration:.1f}s (threshold: {slow_threshold:.0f}s)")

    if tool_call_count > 0 and tool_result_count == 0:
        anomalies.append(f"Tool calls ({tool_call_count}) with no tool results")

    return TurnSummary(
        turn_number=turn_number,
        session_id=session_id,
        is_success=is_success,
        tools_used=tools_used,
        tool_call_count=tool_call_count,
        tool_result_count=tool_result_count,
        cost_usd=cost_usd,
        duration_seconds=duration,
        result_text=result_text,
        anomalies=anomalies,
        num_turns_reported=num_turns_reported,
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/test_overseer_stream_parser.py -v`
Expected: All 7 tests PASS.

**Step 5: Commit**

```bash
git add scripts/overseer/ tests/unit_tests/test_overseer_stream_parser.py
git commit -m "feat: add stream-json parser for overseer e2e tests"
```

---

### Task 3: Session Driver

Drives the Claude Code CLI inside the Docker sandbox container. Handles `docker exec`, captures output, and manages session resumption.

**Files:**
- Create: `scripts/overseer/session_driver.py`
- Test: `tests/unit_tests/test_overseer_session_driver.py`

**Step 1: Write the failing tests**

```python
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from scripts.overseer.session_driver import SessionDriver


def test_build_first_turn_command():
    driver = SessionDriver(container_name="sandbox", gateway_url="http://gateway:8000", api_key="sk-test")
    cmd = driver._build_command("Build a calculator", session_id=None)
    assert "claude" in cmd
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--resume" not in cmd


def test_build_resume_command():
    driver = SessionDriver(container_name="sandbox", gateway_url="http://gateway:8000", api_key="sk-test")
    cmd = driver._build_command("Continue working", session_id="abc-123")
    assert "--resume" in cmd
    idx = cmd.index("--resume")
    assert cmd[idx + 1] == "abc-123"


def test_build_command_includes_verbose():
    driver = SessionDriver(container_name="sandbox", gateway_url="http://gateway:8000", api_key="sk-test")
    cmd = driver._build_command("Do something", session_id=None)
    assert "--verbose" in cmd
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/test_overseer_session_driver.py -v`
Expected: ImportError.

**Step 3: Implement the session driver**

```python
"""Drive Claude Code CLI sessions inside the Docker sandbox container."""

import asyncio
import time

from scripts.overseer.stream_parser import TurnSummary, summarize_turn


class SessionDriver:
    """Manages Claude Code CLI execution inside a Docker container."""

    def __init__(
        self,
        container_name: str,
        gateway_url: str,
        api_key: str,
        timeout_seconds: int = 300,
    ):
        self.container_name = container_name
        self.gateway_url = gateway_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self.turn_count = 0

    def _build_command(self, prompt: str, session_id: str | None = None) -> list[str]:
        """Build the claude CLI command for docker exec."""
        cmd = [
            "claude", "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if session_id:
            cmd.extend(["--resume", session_id])
        cmd.append(prompt)
        return cmd

    async def run_turn(self, prompt: str) -> TurnSummary:
        """Execute one turn of the Claude Code session inside the sandbox."""
        self.turn_count += 1
        cmd = self._build_command(prompt, self.session_id)

        docker_cmd = [
            "docker", "compose", "exec", "-T",
            "-e", f"ANTHROPIC_BASE_URL={self.gateway_url}",
            "-e", f"ANTHROPIC_API_KEY={self.api_key}",
            self.container_name,
        ] + cmd

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=self.timeout_seconds,
        )
        end_time = time.monotonic()

        raw_output = stdout.decode()
        summary = summarize_turn(raw_output, self.turn_count, start_time, end_time)

        if proc.returncode != 0 and proc.returncode is not None:
            summary.anomalies.append(f"Non-zero exit code: {proc.returncode}")
            stderr_text = stderr.decode()[:500]
            if stderr_text:
                summary.anomalies.append(f"Stderr: {stderr_text}")

        if summary.session_id and not self.session_id:
            self.session_id = summary.session_id

        return summary
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/test_overseer_session_driver.py -v`
Expected: All 3 tests PASS.

**Step 5: Commit**

```bash
git add scripts/overseer/session_driver.py tests/unit_tests/test_overseer_session_driver.py
git commit -m "feat: add session driver for overseer e2e tests"
```

---

### Task 4: Overseer LLM

Calls the Anthropic API directly (not through the proxy) to analyze turn output and decide the next prompt.

**Files:**
- Create: `scripts/overseer/overseer_llm.py`
- Test: `tests/unit_tests/test_overseer_llm.py`

**Step 1: Write the failing tests**

```python
from scripts.overseer.overseer_llm import build_analysis_prompt, parse_overseer_response
from scripts.overseer.stream_parser import TurnSummary


def test_build_analysis_prompt_includes_turn_summary():
    summary = TurnSummary(
        turn_number=3, session_id="s1", is_success=True,
        tools_used=["Read", "Bash"], tool_call_count=2, tool_result_count=2,
        cost_usd=0.03, duration_seconds=5.0, result_text="I read the files.",
        anomalies=[], num_turns_reported=3,
    )
    prompt = build_analysis_prompt(summary, task="Build a calculator")
    assert "Turn 3" in prompt
    assert "Read" in prompt
    assert "Build a calculator" in prompt


def test_build_analysis_prompt_includes_anomalies():
    summary = TurnSummary(
        turn_number=1, session_id="s1", is_success=False,
        tools_used=[], tool_call_count=0, tool_result_count=0,
        cost_usd=0.0, duration_seconds=120.0,
        result_text="Error: connection refused",
        anomalies=["Slow turn: 120.0s", "Error result: connection refused"],
        num_turns_reported=0,
    )
    prompt = build_analysis_prompt(summary, task="Build a calculator")
    assert "ANOMALIES" in prompt or "anomalies" in prompt.lower()
    assert "Slow turn" in prompt


def test_parse_overseer_response_extracts_fields():
    response_text = """## Analysis
The turn completed successfully. No proxy issues detected.

## Anomalies
None

## Next Prompt
Now write unit tests for the calculator functions you just created."""

    result = parse_overseer_response(response_text)
    assert "unit tests" in result.next_prompt.lower()
    assert len(result.anomalies) == 0


def test_parse_overseer_response_with_anomalies():
    response_text = """## Analysis
The response appears truncated mid-sentence.

## Anomalies
- Response truncated: the assistant stopped mid-word
- Possible streaming issue

## Next Prompt
Can you continue where you left off? Please finish the implementation."""

    result = parse_overseer_response(response_text)
    assert len(result.anomalies) == 2
    assert "truncated" in result.anomalies[0].lower()
    assert "continue" in result.next_prompt.lower()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/test_overseer_llm.py -v`
Expected: ImportError.

**Step 3: Implement the overseer LLM**

```python
"""Overseer LLM — analyzes turn output and generates next prompts via direct Anthropic API."""

import os
import re
from dataclasses import dataclass, field

import anthropic

from scripts.overseer.stream_parser import TurnSummary

OVERSEER_SYSTEM_PROMPT = """\
You are a test overseer monitoring a Claude Code session running through a proxy gateway.
Your job is to:
1. Analyze the turn output for signs of proxy/gateway issues (NOT code quality issues)
2. Generate the next prompt to keep the session productive and exercising different proxy features

Focus on proxy reliability issues:
- Streaming errors or truncation
- Tool calls that didn't get results
- Session state corruption
- Unexpected errors from the gateway
- Cost or latency anomalies

Do NOT comment on the quality of code the session is writing. That's not your concern.

Respond in this exact format:

## Analysis
[1-2 sentences about what happened in this turn from a proxy health perspective]

## Anomalies
[List each anomaly as a bullet point, or "None" if no issues]

## Next Prompt
[The exact prompt to send for the next turn. Keep the session productive — ask it to build more features, write tests, refactor, use different tools, etc.]
"""


@dataclass
class OverseerAnalysis:
    """Result of the overseer LLM analyzing a turn."""

    analysis: str
    anomalies: list[str]
    next_prompt: str


def build_analysis_prompt(summary: TurnSummary, task: str) -> str:
    """Build the prompt to send to the overseer LLM."""
    lines = [
        f"Original task: {task}",
        f"Turn {summary.turn_number} summary:",
        f"  Session ID: {summary.session_id}",
        f"  Success: {summary.is_success}",
        f"  Tools used: {', '.join(summary.tools_used) or 'none'}",
        f"  Tool calls: {summary.tool_call_count}, Tool results: {summary.tool_result_count}",
        f"  Cost: ${summary.cost_usd:.4f}",
        f"  Duration: {summary.duration_seconds:.1f}s",
        f"  Result text (first 500 chars): {summary.result_text[:500]}",
    ]
    if summary.anomalies:
        lines.append("  ANOMALIES DETECTED:")
        for a in summary.anomalies:
            lines.append(f"    - {a}")
    return "\n".join(lines)


def parse_overseer_response(response_text: str) -> OverseerAnalysis:
    """Parse the structured response from the overseer LLM."""
    analysis = ""
    anomalies: list[str] = []
    next_prompt = ""

    analysis_match = re.search(r"## Analysis\s*\n(.*?)(?=\n## )", response_text, re.DOTALL)
    if analysis_match:
        analysis = analysis_match.group(1).strip()

    anomalies_match = re.search(r"## Anomalies\s*\n(.*?)(?=\n## )", response_text, re.DOTALL)
    if anomalies_match:
        anomalies_text = anomalies_match.group(1).strip()
        if anomalies_text.lower() != "none":
            anomalies = [line.lstrip("- ").strip() for line in anomalies_text.split("\n") if line.strip().startswith("-")]

    prompt_match = re.search(r"## Next Prompt\s*\n(.*)", response_text, re.DOTALL)
    if prompt_match:
        next_prompt = prompt_match.group(1).strip()

    return OverseerAnalysis(analysis=analysis, anomalies=anomalies, next_prompt=next_prompt)


async def analyze_turn(
    summary: TurnSummary,
    task: str,
    model: str = "claude-haiku-4-5-20251001",
) -> OverseerAnalysis:
    """Call the Anthropic API directly to analyze a turn and get the next prompt."""
    client = anthropic.AsyncAnthropic()  # uses ANTHROPIC_API_KEY from env

    prompt = build_analysis_prompt(summary, task)
    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=OVERSEER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text
    return parse_overseer_response(response_text)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/test_overseer_llm.py -v`
Expected: All 4 tests PASS (the tests only exercise `build_analysis_prompt` and `parse_overseer_response`, not the async API call).

**Step 5: Commit**

```bash
git add scripts/overseer/overseer_llm.py tests/unit_tests/test_overseer_llm.py
git commit -m "feat: add overseer LLM for turn analysis and prompt generation"
```

---

### Task 5: Report Server

Tiny aiohttp server that serves a live-updating HTML dashboard via SSE.

**Files:**
- Create: `scripts/overseer/report_server.py`
- Test: `tests/unit_tests/test_overseer_report_server.py`

**Step 1: Write the failing tests**

```python
from scripts.overseer.report_server import ReportServer, build_dashboard_html
from scripts.overseer.stream_parser import TurnSummary


def test_build_dashboard_html_renders():
    html = build_dashboard_html()
    assert "<html" in html
    assert "EventSource" in html  # SSE client
    assert "overseer" in html.lower() or "dashboard" in html.lower()


def test_report_server_add_turn():
    server = ReportServer(port=0)  # port 0 = don't actually bind
    summary = TurnSummary(
        turn_number=1, session_id="s1", is_success=True,
        tools_used=["Read"], tool_call_count=1, tool_result_count=1,
        cost_usd=0.01, duration_seconds=3.0, result_text="Done.",
        anomalies=[], num_turns_reported=1,
    )
    server.add_turn(summary)
    assert len(server.turns) == 1
    assert server.total_cost == 0.01


def test_report_server_add_turn_with_anomaly():
    server = ReportServer(port=0)
    summary = TurnSummary(
        turn_number=1, session_id="s1", is_success=False,
        tools_used=[], tool_call_count=0, tool_result_count=0,
        cost_usd=0.0, duration_seconds=120.0, result_text="Error",
        anomalies=["Slow turn: 120.0s"], num_turns_reported=0,
    )
    server.add_turn(summary)
    assert len(server.all_anomalies) == 1


def test_report_server_state_as_json():
    server = ReportServer(port=0)
    state = server.state_as_json()
    assert "turns" in state
    assert "total_cost" in state
    assert "status" in state
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/test_overseer_report_server.py -v`
Expected: ImportError.

**Step 3: Implement the report server**

Use the `visual-explainer` skill to generate the dashboard HTML. The server code itself:

```python
"""Live-updating HTML dashboard for overseer sessions via SSE."""

import asyncio
import json
from collections import Counter

from aiohttp import web

from scripts.overseer.stream_parser import TurnSummary


class ReportServer:
    """Serves a live dashboard and pushes updates via SSE."""

    def __init__(self, port: int = 8080):
        self.port = port
        self.turns: list[TurnSummary] = []
        self.all_anomalies: list[dict] = []
        self.total_cost: float = 0.0
        self.status: str = "starting"
        self.task: str = ""
        self._sse_queues: list[asyncio.Queue] = []
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    def add_turn(self, summary: TurnSummary, overseer_analysis: str = "") -> None:
        self.turns.append(summary)
        self.total_cost += summary.cost_usd
        for anomaly in summary.anomalies:
            self.all_anomalies.append({
                "turn": summary.turn_number,
                "source": "rule",
                "message": anomaly,
            })
        self._broadcast(self.state_as_json())

    def add_llm_anomalies(self, turn_number: int, anomalies: list[str]) -> None:
        for anomaly in anomalies:
            self.all_anomalies.append({
                "turn": turn_number,
                "source": "llm",
                "message": anomaly,
            })
        if anomalies:
            self._broadcast(self.state_as_json())

    def set_status(self, status: str) -> None:
        self.status = status
        self._broadcast(self.state_as_json())

    def state_as_json(self) -> str:
        tool_counts = Counter()
        for turn in self.turns:
            for tool in turn.tools_used:
                tool_counts[tool] += 1

        return json.dumps({
            "status": self.status,
            "task": self.task,
            "turn_count": len(self.turns),
            "total_cost": round(self.total_cost, 4),
            "anomaly_count": len(self.all_anomalies),
            "anomalies": self.all_anomalies[-20:],  # last 20
            "tool_counts": dict(tool_counts),
            "turns": [
                {
                    "number": t.turn_number,
                    "success": t.is_success,
                    "tools": t.tools_used,
                    "cost": round(t.cost_usd, 4),
                    "duration": round(t.duration_seconds, 1),
                    "anomalies": t.anomalies,
                    "result_preview": t.result_text[:150],
                }
                for t in self.turns
            ],
        })

    def _broadcast(self, data: str) -> None:
        for queue in self._sse_queues:
            queue.put_nowait(data)

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=build_dashboard_html(), content_type="text/html")

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        await response.prepare(request)

        queue: asyncio.Queue = asyncio.Queue()
        self._sse_queues.append(queue)

        # Send current state immediately
        await response.write(f"data: {self.state_as_json()}\n\n".encode())

        try:
            while True:
                data = await queue.get()
                await response.write(f"data: {data}\n\n".encode())
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self._sse_queues.remove(queue)
        return response

    async def start(self) -> None:
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/events", self._handle_sse)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


def build_dashboard_html() -> str:
    """Generate the self-contained HTML dashboard with SSE client."""
    # This will be generated using the visual-explainer skill during implementation.
    # For now, a minimal working version:
    return """<!DOCTYPE html>
<html><head><title>Overseer Dashboard</title>
<style>
  body { font-family: system-ui; margin: 2rem; background: #0f172a; color: #e2e8f0; }
  .status { font-size: 1.5rem; margin-bottom: 1rem; }
  .metrics { display: flex; gap: 2rem; margin-bottom: 2rem; }
  .metric { background: #1e293b; padding: 1rem; border-radius: 8px; min-width: 120px; }
  .metric-value { font-size: 2rem; font-weight: bold; color: #38bdf8; }
  .metric-label { font-size: 0.875rem; color: #94a3b8; }
  .anomaly { background: #451a03; border-left: 3px solid #f97316; padding: 0.5rem 1rem; margin: 0.25rem 0; }
  .turn { background: #1e293b; padding: 0.75rem 1rem; margin: 0.25rem 0; border-radius: 4px; }
  .turn.error { border-left: 3px solid #ef4444; }
  .turn.ok { border-left: 3px solid #22c55e; }
</style>
</head><body>
<h1>Overseer Dashboard</h1>
<div class="status" id="status">Connecting...</div>
<div class="metrics">
  <div class="metric"><div class="metric-value" id="turns">0</div><div class="metric-label">Turns</div></div>
  <div class="metric"><div class="metric-value" id="cost">$0.00</div><div class="metric-label">Cost</div></div>
  <div class="metric"><div class="metric-value" id="anomalies">0</div><div class="metric-label">Anomalies</div></div>
</div>
<h2>Anomalies</h2>
<div id="anomaly-list"></div>
<h2>Turns</h2>
<div id="turn-list"></div>
<script>
const es = new EventSource('/events');
es.onmessage = (e) => {
  const d = JSON.parse(e.data);
  document.getElementById('status').textContent = d.status;
  document.getElementById('turns').textContent = d.turn_count;
  document.getElementById('cost').textContent = '$' + d.total_cost.toFixed(2);
  document.getElementById('anomalies').textContent = d.anomaly_count;
  const al = document.getElementById('anomaly-list');
  al.innerHTML = d.anomalies.map(a =>
    '<div class="anomaly">Turn ' + a.turn + ' [' + a.source + ']: ' + a.message + '</div>'
  ).join('');
  const tl = document.getElementById('turn-list');
  tl.innerHTML = d.turns.slice().reverse().map(t =>
    '<div class="turn ' + (t.success ? 'ok' : 'error') + '">' +
    '<strong>Turn ' + t.number + '</strong> | ' +
    t.tools.join(', ') + ' | ' +
    t.duration + 's | $' + t.cost.toFixed(4) +
    (t.anomalies.length ? ' | ⚠ ' + t.anomalies.join(', ') : '') +
    '<br><small>' + t.result_preview + '</small></div>'
  ).join('');
};
</script>
</body></html>"""
```

Note: `aiohttp` needs to be added as a dependency. Add to pyproject.toml `dependencies` list: `"aiohttp>=3.9.0"`.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/test_overseer_report_server.py -v`
Expected: All 4 tests PASS.

**Step 5: Commit**

```bash
git add scripts/overseer/report_server.py tests/unit_tests/test_overseer_report_server.py
git commit -m "feat: add live report server for overseer dashboard"
```

---

### Task 6: Main Overseer Script

Wire everything together into the CLI entry point.

**Files:**
- Create: `scripts/overseer/main.py`
- No unit tests for this module — it's pure orchestration glue. Integration tested by running it.

**Step 1: Implement main.py**

```python
"""Overseer: multi-turn e2e proxy testing with live dashboard.

Usage:
    python -m scripts.overseer.main --task "Build a Python CLI calculator with tests"
"""

import argparse
import asyncio
import logging
import signal
import subprocess
import sys
import time

from scripts.overseer.overseer_llm import analyze_turn
from scripts.overseer.report_server import ReportServer
from scripts.overseer.session_driver import SessionDriver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("overseer")

CONTAINER_NAME = "sandbox"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overseer: multi-turn e2e proxy testing")
    parser.add_argument("--task", required=True, help="Initial task prompt for the Claude Code session")
    parser.add_argument("--max-turns", type=int, default=20, help="Stop after N turns (default: 20)")
    parser.add_argument("--timeout", type=int, default=600, help="Stop after N seconds (default: 600)")
    parser.add_argument("--port", type=int, default=8080, help="Report server port (default: 8080)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Overseer LLM model")
    parser.add_argument("--gateway-url", default="http://gateway:8000", help="Proxy URL from container perspective")
    parser.add_argument("--api-key", default=None, help="API key for proxy (default: from PROXY_API_KEY env)")
    parser.add_argument("--turn-timeout", type=int, default=300, help="Timeout per turn in seconds (default: 300)")
    return parser.parse_args()


def ensure_sandbox_running() -> None:
    """Start the sandbox container if not already running."""
    result = subprocess.run(
        ["docker", "compose", "--profile", "overseer", "up", "-d", "sandbox"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("Failed to start sandbox: %s", result.stderr)
        sys.exit(1)
    log.info("Sandbox container is running")


async def run_overseer(args: argparse.Namespace) -> None:
    api_key = args.api_key or __import__("os").environ.get("PROXY_API_KEY", "sk-luthien-dev-key")

    ensure_sandbox_running()

    report = ReportServer(port=args.port)
    report.task = args.task
    await report.start()
    log.info("Dashboard: http://localhost:%d", args.port)

    driver = SessionDriver(
        container_name=CONTAINER_NAME,
        gateway_url=args.gateway_url,
        api_key=api_key,
        timeout_seconds=args.turn_timeout,
    )

    report.set_status("running")
    start_time = time.monotonic()
    current_prompt = args.task

    try:
        for turn in range(1, args.max_turns + 1):
            elapsed = time.monotonic() - start_time
            if elapsed > args.timeout:
                log.info("Timeout reached after %.0fs", elapsed)
                break

            log.info("Turn %d: sending prompt (%.0f chars)", turn, len(current_prompt))
            summary = await driver.run_turn(current_prompt)
            report.add_turn(summary)

            if summary.anomalies:
                for a in summary.anomalies:
                    log.warning("Turn %d anomaly: %s", turn, a)

            # Ask overseer LLM for analysis and next prompt
            analysis = await analyze_turn(summary, args.task, model=args.model)
            report.add_llm_anomalies(turn, analysis.anomalies)

            if analysis.anomalies:
                for a in analysis.anomalies:
                    log.warning("Turn %d LLM anomaly: %s", turn, a)

            log.info("Turn %d complete: %s, cost=$%.4f, %.1fs",
                     turn, "OK" if summary.is_success else "ERROR",
                     summary.cost_usd, summary.duration_seconds)

            current_prompt = analysis.next_prompt
            if not current_prompt:
                log.info("Overseer LLM returned empty prompt, stopping")
                break

    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception as e:
        log.error("Overseer error: %s", e, exc_info=True)
        report.set_status(f"error: {e}")
    finally:
        report.set_status("finished")
        log.info("Session complete: %d turns, $%.4f total, %d anomalies",
                 len(report.turns), report.total_cost, len(report.all_anomalies))
        log.info("Dashboard still running at http://localhost:%d (Ctrl+C to stop)", args.port)

        # Keep server alive so user can inspect the report
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await report.stop()


def main() -> None:
    args = parse_args()
    asyncio.run(run_overseer(args))


if __name__ == "__main__":
    main()
```

**Step 2: Verify it at least parses**

Run: `python -c "from scripts.overseer.main import parse_args; print('OK')"`
Expected: "OK"

**Step 3: Commit**

```bash
git add scripts/overseer/main.py
git commit -m "feat: add overseer main script wiring everything together"
```

---

### Task 7: Add aiohttp Dependency and Wire Up

**Files:**
- Modify: `pyproject.toml` (add aiohttp)

**Step 1: Add aiohttp to dependencies**

In `pyproject.toml`, add `"aiohttp>=3.9.0"` to the `dependencies` list.

**Step 2: Sync**

Run: `uv sync --dev`

**Step 3: Run all overseer tests**

Run: `uv run pytest tests/unit_tests/test_overseer_*.py -v`
Expected: All tests PASS.

**Step 4: Run format and lint**

Run: `./scripts/format_all.sh && uv run ruff check scripts/overseer/ tests/unit_tests/test_overseer_*.py`

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add aiohttp dependency for overseer report server"
```

---

### Task 8: Smoke Test — End to End

Requires gateway running. This is manual verification, not an automated test.

**Step 1: Start the full stack + sandbox**

```bash
./scripts/quick_start.sh
docker compose --profile overseer up -d sandbox
```

**Step 2: Run the overseer for 3 turns**

```bash
python -m scripts.overseer.main \
  --task "Create a Python file that prints hello world, then read it back" \
  --max-turns 3 \
  --port 8080
```

**Step 3: Verify**

- Open `http://localhost:8080` in a browser
- Confirm dashboard shows turns updating live
- Confirm no crashes, session_id captured, turns increment
- Ctrl+C to stop

**Step 4: Commit any fixes discovered during smoke test**

---

### Task 9: Enhance Dashboard with visual-explainer

Use the `visual-explainer` skill to generate a polished dashboard HTML that replaces the minimal one in `build_dashboard_html()`.

The dashboard should include:
- Per-turn latency chart (bar chart)
- Tool usage frequency (horizontal bar)
- Anomaly timeline
- Cost accumulator line
- Current turn details panel
- Color-coded turn cards (green = ok, red = error, yellow = anomaly)

**Step 1: Generate dashboard HTML using visual-explainer skill**

Invoke `visual-explainer` with the dashboard requirements. Paste the generated HTML into `build_dashboard_html()` in `report_server.py`.

**Step 2: Smoke test the new dashboard**

Run the overseer for 3 turns and verify the dashboard renders correctly.

**Step 3: Commit**

```bash
git add scripts/overseer/report_server.py
git commit -m "feat: polish overseer dashboard with charts and visual design"
```
