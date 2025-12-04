# ABOUTME: E2E tests that invoke Claude Code CLI through the gateway
# ABOUTME: Tests tool-use flows by parsing stream-json output from `claude -p` mode

"""E2E tests for Claude Code CLI routed through the gateway.

These tests invoke the `claude` CLI in non-interactive (-p) mode with the gateway
as the base URL. The stream-json output is parsed to verify:
- Requests flow through the gateway successfully
- Tool use events are captured and structured correctly
- Multi-turn tool interactions complete properly
- Different policies affect tool call behavior appropriately

Prerequisites:
- `claude` CLI must be installed (npm install -g @anthropic-ai/claude-cli)
- Gateway must be running (docker compose up v2-gateway)
- Valid API credentials in env or .env
"""

import asyncio
import json
import os
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000/")
API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))


@dataclass
class ClaudeCodeEvent:
    """A parsed event from claude stream-json output."""

    type: str
    subtype: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def is_init(self) -> bool:
        return self.type == "system" and self.subtype == "init"

    @property
    def is_assistant(self) -> bool:
        return self.type == "assistant"

    @property
    def is_user(self) -> bool:
        return self.type == "user"

    @property
    def is_result(self) -> bool:
        return self.type == "result"

    @property
    def is_success(self) -> bool:
        return self.is_result and self.subtype == "success"

    @property
    def is_error(self) -> bool:
        return self.is_result and self.raw.get("is_error", False)

    def get_tool_uses(self) -> list[dict]:
        """Extract tool_use blocks from assistant message."""
        if not self.is_assistant:
            return []
        message = self.raw.get("message", {})
        content = message.get("content", [])
        return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]

    def get_tool_results(self) -> list[dict]:
        """Extract tool_result blocks from user message."""
        if not self.is_user:
            return []
        message = self.raw.get("message", {})
        content = message.get("content", [])
        return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]

    def get_text_content(self) -> str:
        """Extract text content from assistant message."""
        if not self.is_assistant:
            return ""
        message = self.raw.get("message", {})
        content = message.get("content", [])
        texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return " ".join(texts)


@dataclass
class ClaudeCodeResult:
    """Result of running claude CLI in print mode."""

    events: list[ClaudeCodeEvent]
    final_result: str
    is_success: bool
    num_turns: int
    cost_usd: float
    session_id: str
    raw_output: str
    stderr: str

    @property
    def init_event(self) -> ClaudeCodeEvent | None:
        for event in self.events:
            if event.is_init:
                return event
        return None

    @property
    def tool_uses(self) -> list[dict]:
        """All tool uses across all assistant messages."""
        uses = []
        for event in self.events:
            uses.extend(event.get_tool_uses())
        return uses

    @property
    def tool_results(self) -> list[dict]:
        """All tool results across all user messages."""
        results = []
        for event in self.events:
            results.extend(event.get_tool_results())
        return results

    def tools_used(self) -> set[str]:
        """Set of tool names that were invoked."""
        return {use.get("name", "") for use in self.tool_uses}


def parse_stream_json(output: str) -> list[ClaudeCodeEvent]:
    """Parse JSONL stream-json output into events."""
    events = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
            event = ClaudeCodeEvent(
                type=data.get("type", "unknown"),
                subtype=data.get("subtype"),
                raw=data,
            )
            events.append(event)
        except json.JSONDecodeError:
            continue
    return events


async def run_claude_code(
    prompt: str,
    tools: list[str] | None = None,
    max_turns: int = 5,
    timeout_seconds: int = 120,
    gateway_url: str = GATEWAY_URL,
    api_key: str = API_KEY,
    system_prompt: str | None = None,
    working_dir: str | None = None,
) -> ClaudeCodeResult:
    """Run claude CLI in print mode and parse the output.

    Args:
        prompt: The prompt to send to Claude
        tools: List of tool names to enable (e.g., ["Read", "Bash(ls:*)"])
        max_turns: Maximum number of agentic turns
        timeout_seconds: Command timeout
        gateway_url: Base URL for Anthropic API (gateway)
        api_key: API key for authentication
        system_prompt: Optional system prompt override
        working_dir: Working directory for claude command

    Returns:
        ClaudeCodeResult with parsed events and metadata
    """
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]

    if tools:
        cmd.extend(["--tools", " ".join(tools)])

    cmd.extend(["--max-turns", str(max_turns)])

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url
    env["ANTHROPIC_AUTH_TOKEN"] = api_key

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=working_dir,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(prompt.encode()),
        timeout=timeout_seconds,
    )

    raw_output = stdout.decode()
    stderr_output = stderr.decode()

    events = parse_stream_json(raw_output)

    final_result = ""
    is_success = False
    num_turns = 0
    cost_usd = 0.0
    session_id = ""

    for event in events:
        if event.is_result:
            final_result = event.raw.get("result", "")
            is_success = event.subtype == "success" and not event.raw.get("is_error", False)
            num_turns = event.raw.get("num_turns", 0)
            cost_usd = event.raw.get("total_cost_usd", 0.0)
            session_id = event.raw.get("session_id", "")

    return ClaudeCodeResult(
        events=events,
        final_result=final_result,
        is_success=is_success,
        num_turns=num_turns,
        cost_usd=cost_usd,
        session_id=session_id,
        raw_output=raw_output,
        stderr=stderr_output,
    )


@pytest.fixture
def claude_available():
    """Check if claude CLI is available."""
    if not shutil.which("claude"):
        pytest.skip("Claude CLI not installed - run: npm install -g @anthropic-ai/claude-cli")


@pytest.fixture
async def gateway_healthy():
    """Check if gateway is running and healthy."""
    gateway_base = GATEWAY_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{gateway_base}/health")
            if response.status_code != 200:
                pytest.skip(f"Gateway not healthy: {response.status_code}")
        except httpx.ConnectError:
            pytest.skip(f"Gateway not running at {gateway_base}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_basic_request(claude_available, gateway_healthy):
    """Test basic Claude Code request flows through gateway."""
    result = await run_claude_code(
        prompt="What is 2 + 2? Reply with just the number.",
        tools=[],
        max_turns=1,
    )

    assert result.is_success, f"Request failed: {result.stderr}"
    assert result.init_event is not None, "Should have init event"
    assert "4" in result.final_result, f"Expected '4' in result: {result.final_result}"
    assert result.num_turns >= 1


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_tool_use_read(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code can use Read tool through gateway."""
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("Hello from test file!")

    # Don't restrict tools - use default tool set which includes Read
    result = await run_claude_code(
        prompt=f"Read the file at {test_file} and tell me exactly what it says without paraphrasing. Be brief.",
        tools=None,  # Use default tools
        max_turns=3,
        working_dir=str(tmp_path),
    )

    assert result.is_success, f"Request failed: {result.stderr}"
    assert "Read" in result.tools_used(), f"Expected Read tool use, got: {result.tools_used()}"
    assert len(result.tool_results) > 0, "Should have tool results"
    assert "Hello from test file" in result.final_result or "Hello" in result.final_result


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_tool_use_bash(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code can use Bash tool through gateway."""
    # Don't restrict tools - use default tool set which includes Bash
    result = await run_claude_code(
        prompt="Run 'echo hello_from_bash' and tell me what it outputs. Be brief.",
        tools=None,  # Use default tools
        max_turns=3,
        working_dir=str(tmp_path),
    )

    assert result.is_success, f"Request failed: {result.stderr}"

    bash_uses = [u for u in result.tool_uses if "bash" in u.get("name", "").lower()]
    assert len(bash_uses) > 0, f"Expected Bash tool use, got: {result.tools_used()}"
    assert "hello_from_bash" in result.final_result.lower() or any(
        "hello_from_bash" in str(r) for r in result.tool_results
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_multi_turn_tool_use(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code multi-turn tool interactions through gateway."""
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.write_text("Content A")
    file2.write_text("Content B")

    result = await run_claude_code(
        prompt=f"Read both {file1} and {file2}, then tell me what's in each. Be brief.",
        tools=None,  # Use default tools
        max_turns=5,
        working_dir=str(tmp_path),
    )

    assert result.is_success, f"Request failed: {result.stderr}"
    assert result.num_turns >= 2, f"Expected multi-turn, got {result.num_turns} turns"
    assert len(result.tool_uses) >= 2, f"Expected multiple tool uses: {len(result.tool_uses)}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_events_structure(claude_available, gateway_healthy):
    """Test that stream-json events have expected structure."""
    result = await run_claude_code(
        prompt="Say hello briefly.",
        tools=[],
        max_turns=1,
    )

    assert result.is_success
    assert len(result.events) > 0

    init_event = result.init_event
    assert init_event is not None
    assert "session_id" in init_event.raw
    assert "model" in init_event.raw

    assistant_events = [e for e in result.events if e.is_assistant]
    assert len(assistant_events) > 0, "Should have assistant events"

    result_events = [e for e in result.events if e.is_result]
    assert len(result_events) == 1, "Should have exactly one result event"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_session_tracking(claude_available, gateway_healthy):
    """Test that session IDs are consistent across events."""
    result = await run_claude_code(
        prompt="What is the capital of France? Brief answer.",
        tools=[],
        max_turns=1,
    )

    assert result.is_success
    assert result.session_id, "Should have session ID"

    session_ids = set()
    for event in result.events:
        if "session_id" in event.raw:
            session_ids.add(event.raw["session_id"])

    assert len(session_ids) == 1, f"All events should have same session ID: {session_ids}"
    assert result.session_id in session_ids


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_cost_tracking(claude_available, gateway_healthy):
    """Test that cost information is captured."""
    result = await run_claude_code(
        prompt="Say one word.",
        tools=[],
        max_turns=1,
    )

    assert result.is_success
    assert result.cost_usd > 0, f"Should have non-zero cost: {result.cost_usd}"

    result_event = next((e for e in result.events if e.is_result), None)
    assert result_event is not None
    assert "usage" in result_event.raw or "modelUsage" in result_event.raw


# === Policy Management Helpers ===


async def create_and_activate_policy(
    client: httpx.AsyncClient,
    name: str,
    policy_class_ref: str,
    config: dict,
) -> None:
    """Create and activate a policy instance."""
    gateway_base = GATEWAY_URL.rstrip("/")
    admin_headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}

    # Create policy instance
    create_response = await client.post(
        f"{gateway_base}/admin/policy/create",
        headers=admin_headers,
        json={
            "name": name,
            "policy_class_ref": policy_class_ref,
            "config": config,
            "created_by": "claude-code-e2e-test",
        },
    )
    assert create_response.status_code == 200, f"Failed to create policy: {create_response.text}"

    # Activate policy instance
    activate_response = await client.post(
        f"{gateway_base}/admin/policy/activate",
        headers=admin_headers,
        json={"name": name, "activated_by": "claude-code-e2e-test"},
    )
    assert activate_response.status_code == 200, f"Failed to activate policy: {activate_response.text}"

    # Brief pause to ensure policy is active
    time.sleep(0.3)


async def get_current_policy(client: httpx.AsyncClient) -> dict:
    """Get current policy information."""
    gateway_base = GATEWAY_URL.rstrip("/")
    admin_headers = {"Authorization": f"Bearer {ADMIN_API_KEY}"}
    response = await client.get(f"{gateway_base}/admin/policy/current", headers=admin_headers)
    assert response.status_code == 200
    return response.json()


@asynccontextmanager
async def policy_context(policy_class_ref: str, config: dict):
    """Context manager that sets up a policy and restores NoOp after test."""
    instance_name = f"test-{int(time.time() * 1000)}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Activate the test policy
        await create_and_activate_policy(client, instance_name, policy_class_ref, config)
        try:
            yield
        finally:
            # Restore NoOp policy after test
            restore_name = f"restore-noop-{int(time.time() * 1000)}"
            await create_and_activate_policy(
                client,
                restore_name,
                "luthien_proxy.policies.noop_policy:NoOpPolicy",
                {},
            )


# === Policy-Specific Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_with_noop_policy(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code tool use works under NoOpPolicy."""
    async with policy_context("luthien_proxy.policies.noop_policy:NoOpPolicy", {}):
        test_file = tmp_path / "noop_test.txt"
        test_file.write_text("NoOp policy test content")

        result = await run_claude_code(
            prompt=f"Read the file at {test_file} and tell me what it says. Be brief.",
            tools=None,
            max_turns=3,
            working_dir=str(tmp_path),
        )

        assert result.is_success, f"Request failed under NoOpPolicy: {result.stderr}"
        assert "Read" in result.tools_used(), f"Expected Read tool use, got: {result.tools_used()}"
        assert "NoOp policy test" in result.final_result or "noop" in result.final_result.lower()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_with_simple_noop_policy(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code tool use works under SimpleNoOpPolicy."""
    async with policy_context("luthien_proxy.policies.simple_noop_policy:SimpleNoOpPolicy", {}):
        test_file = tmp_path / "simple_noop_test.txt"
        test_file.write_text("SimpleNoOp policy test content")

        result = await run_claude_code(
            prompt=f"Read the file at {test_file} and tell me what it says. Be brief.",
            tools=None,
            max_turns=3,
            working_dir=str(tmp_path),
        )

        assert result.is_success, f"Request failed under SimpleNoOpPolicy: {result.stderr}"
        assert "Read" in result.tools_used(), f"Expected Read tool use, got: {result.tools_used()}"
        assert "SimpleNoOp policy test" in result.final_result or "simple" in result.final_result.lower()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_with_tool_judge_high_threshold(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code tool use allowed under ToolCallJudgePolicy with high threshold (0.99).

    With threshold=0.99, almost all tool calls should be allowed since the judge
    would need to be 99% confident the call is harmful to block it.
    """
    async with policy_context(
        "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
        {
            "model": "claude-haiku-4-5",
            "probability_threshold": 0.99,  # Very high - should allow most calls
            "temperature": 0.0,
            "max_tokens": 256,
        },
    ):
        test_file = tmp_path / "judge_allow_test.txt"
        test_file.write_text("Content that should be readable")

        result = await run_claude_code(
            prompt=f"Read the file at {test_file} and tell me what it says. Be brief.",
            tools=None,
            max_turns=5,
            working_dir=str(tmp_path),
        )

        assert result.is_success, f"Request failed under ToolCallJudgePolicy (high threshold): {result.stderr}"
        # With high threshold, tool calls should succeed
        assert len(result.tool_results) > 0, "Should have tool results (tool call allowed)"
        assert "should be readable" in result.final_result.lower() or "content" in result.final_result.lower()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_with_tool_judge_low_threshold(claude_available, gateway_healthy, tmp_path):
    """Test Claude Code tool use blocked under ToolCallJudgePolicy with low threshold (0.01).

    With threshold=0.01, most tool calls should be blocked since even 1% confidence
    that the call might be harmful triggers a block.
    """
    async with policy_context(
        "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
        {
            "model": "claude-haiku-4-5",
            "probability_threshold": 0.01,  # Very low - should block most calls
            "temperature": 0.0,
            "max_tokens": 256,
            "blocked_message_template": "⛔ TEST_BLOCK: Tool '{tool_name}' rejected by judge",
        },
    ):
        test_file = tmp_path / "judge_block_test.txt"
        test_file.write_text("Content that should be blocked from reading")

        result = await run_claude_code(
            prompt=f"Read the file at {test_file} and tell me what it says. Be brief.",
            tools=None,
            max_turns=5,
            working_dir=str(tmp_path),
        )

        # The request should still "succeed" from Claude Code's perspective,
        # but the tool call should be blocked by the policy
        assert result.is_success, f"Request failed: {result.stderr}"

        # Check that no tool calls were made (blocked before execution)
        assert len(result.tool_results) == 0, f"Expected no tool results (blocked), got {len(result.tool_results)}"

        # Check that the configured block message appears in output
        assert "⛔ TEST_BLOCK" in result.final_result, (
            f"Expected block message '⛔ TEST_BLOCK' in output, got: {result.final_result[:200]}"
        )
