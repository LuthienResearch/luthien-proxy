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
import uuid
from collections import Counter
from dataclasses import dataclass, field

import pytest
from tests.constants import DEFAULT_CLAUDE_TEST_MODEL

# Import shared fixtures and helpers from conftest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context  # noqa: F401


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
    resume_session_id: str | None = None,
    allowed_tools: list[str] | None = None,
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
        resume_session_id: Session ID to resume (for multi-turn conversations)

    Returns:
        ClaudeCodeResult with parsed events and metadata
    """
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]

    if allowed_tools:
        cmd.extend(["--permission-mode", "dontAsk", "--allowedTools"] + allowed_tools)

    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    if tools:
        cmd.extend(["--tools", " ".join(tools)])

    cmd.extend(["--max-turns", str(max_turns)])

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url
    env["ANTHROPIC_API_KEY"] = api_key
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

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
        if not event.is_result:
            continue
        final_result = event.raw.get("result", "")
        is_success = event.is_success
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


# Fixtures claude_available, codex_available, gateway_healthy, http_client
# are provided by conftest.py and auto-discovered by pytest


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
    assert result_event, "Should have a result event"
    assert "usage" in result_event.raw or "modelUsage" in result_event.raw


# Policy helpers (set_policy, get_current_policy, policy_context)
# are provided by conftest.py


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
            "model": DEFAULT_CLAUDE_TEST_MODEL,
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
    that the call might be harmful triggers a block. The policy emits a replacement
    text block with the blocked message in place of the tool call.
    """
    async with policy_context(
        "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
        {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "probability_threshold": 0.01,  # Very low - should block most calls
            "temperature": 0.0,
            "max_tokens": 256,
            "blocked_message_template": "⛔ TEST_BLOCK: Tool '{tool_name}' rejected by judge",
        },
    ):
        test_file = tmp_path / "judge_block_test.txt"
        test_file.write_text("Content that should be blocked from reading")

        result = await run_claude_code(
            prompt=f"Use the Read tool to read the file at {test_file}. You MUST use the Read tool - do not respond without first calling Read on that exact file path.",
            tools=None,
            max_turns=5,
            working_dir=str(tmp_path),
        )

        # The request should still "succeed" from Claude Code's perspective,
        # but the tool call should be blocked by the policy
        assert result.is_success, f"Request failed: {result.stderr}"

        # Tool calls should be blocked before execution
        assert len(result.tool_results) == 0, f"Expected no tool results (blocked), got {len(result.tool_results)}"

        # Block message should appear in output
        assert "⛔ TEST_BLOCK" in result.final_result, f"Expected block message in output: {result.final_result[:200]}"


# === Multi-turn Session Tests ===


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_claude_code_multiturn_with_compact(claude_available, gateway_healthy, tmp_path):
    """Test a multi-turn Claude Code session with tool calls, /compact, and post-compact interaction.

    This exercises the full session lifecycle through the proxy:
    1. Initial prompt triggers 3+ tool calls (at least 2 of the same tool type)
    2. Resume the session and send /compact to compress context
    3. Resume again and verify the session still functions
    """
    test_id = uuid.uuid4().hex[:8]
    test_dir = tmp_path / f"test_e2e_{test_id}"
    test_dir.mkdir()

    # --- Step 1: Create files and read them back (produces Write x3 + Read x2 tool calls) ---

    step1_prompt = (
        f"Create three files:\n"
        f"  1. {test_dir}/a.txt containing exactly 'hello'\n"
        f"  2. {test_dir}/b.txt containing exactly 'world'\n"
        f"  3. {test_dir}/c.txt containing exactly 'test'\n"
        f"Then read back a.txt and b.txt to confirm their contents. Be brief."
    )

    step1 = await run_claude_code(
        prompt=step1_prompt,
        tools=None,
        max_turns=10,
        timeout_seconds=120,
        working_dir=str(tmp_path),
        allowed_tools=["Write", "Read", "Bash"],
    )

    assert step1.is_success, f"Step 1 failed: {step1.stderr}\nOutput: {step1.raw_output[:500]}"
    assert step1.session_id, "Step 1 must produce a session_id for resumption"

    # Verify at least 3 tool uses total
    assert len(step1.tool_uses) >= 3, (
        f"Expected at least 3 tool uses, got {len(step1.tool_uses)}: {[u.get('name') for u in step1.tool_uses]}"
    )

    # Verify at least 2 uses of the same tool
    tool_counts = Counter(u.get("name", "") for u in step1.tool_uses)
    max_same_tool = max(tool_counts.values())
    assert max_same_tool >= 2, f"Expected at least 2 uses of the same tool, got counts: {dict(tool_counts)}"

    # Verify the files were actually created
    assert (test_dir / "a.txt").exists(), "a.txt should have been created"
    assert (test_dir / "b.txt").exists(), "b.txt should have been created"
    assert (test_dir / "c.txt").exists(), "c.txt should have been created"

    session_id = step1.session_id

    # --- Step 2: Resume session and send /compact ---

    step2 = await run_claude_code(
        prompt="/compact",
        max_turns=1,
        timeout_seconds=120,
        resume_session_id=session_id,
        working_dir=str(tmp_path),
    )

    # /compact should complete without error.
    # It may return is_success=True with an empty result, or produce a compact_boundary event.
    assert step2.is_success or any(e.raw.get("subtype") == "compact_boundary" for e in step2.events), (
        f"Step 2 (/compact) failed: {step2.stderr}\nOutput: {step2.raw_output[:500]}"
    )

    # --- Step 3: Resume again and verify the session still works ---

    step3 = await run_claude_code(
        prompt="What files did you create earlier? List their names briefly.",
        max_turns=3,
        timeout_seconds=120,
        resume_session_id=session_id,
        working_dir=str(tmp_path),
    )

    assert step3.is_success, f"Step 3 (post-compact query) failed: {step3.stderr}\nOutput: {step3.raw_output[:500]}"

    # After compact, Claude may or may not remember exact details.
    # The key assertion: the session is still functional and produces a response.
    assert step3.final_result, "Step 3 should produce a non-empty response"
