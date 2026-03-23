"""Claude Code client simulator for realistic gateway e2e testing.

Unlike direct httpx calls with minimal payloads, ClaudeCodeSimulator builds
realistic Claude Code-style requests:
  - System prompt as a blocks array with cache_control
  - Tool definitions with full input_schema and cache_control
  - stream=True always (Claude Code never uses non-streaming)
  - Multi-turn message history maintained across turns

Usage:
    session = ClaudeCodeSimulator(gateway_url=GATEWAY_URL, api_key=API_KEY)

    # Turn 1: user asks, model calls a tool
    mock_anthropic.enqueue(tool_response("Bash", {"command": "ls -la"}))
    turn1 = await session.send("List the files here")
    assert turn1.tool_calls[0].name == "Bash"

    # Turn 2: provide tool result, model responds with text
    mock_anthropic.enqueue(text_response("I see these files: ..."))
    turn2 = await session.continue_with_tool_result(turn1.tool_calls[0].id, "file.txt\\n")
    assert "file.txt" in turn2.text
"""

import json
from dataclasses import dataclass, field

import httpx

# ---------------------------------------------------------------------------
# Realistic Claude Code tool definitions
# ---------------------------------------------------------------------------

_BASH_TOOL: dict = {
    "name": "Bash",
    "description": (
        "Execute a bash command in the terminal. Use for running scripts, "
        "checking file contents, or interacting with the system."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to run"},
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds",
            },
        },
        "required": ["command"],
    },
}

_READ_TOOL: dict = {
    "name": "Read",
    "description": "Read the contents of a file at the given path.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {"type": "integer", "description": "Line number to start from"},
            "limit": {"type": "integer", "description": "Number of lines to read"},
        },
        "required": ["file_path"],
    },
}

_WRITE_TOOL: dict = {
    "name": "Write",
    "description": "Write content to a file, creating or overwriting it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to write to"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["file_path", "content"],
    },
}

# Default tool set — representative Claude Code subset.
# cache_control on the last tool only (prompt caching convention).
DEFAULT_TOOLS: list[dict] = [
    _BASH_TOOL,
    _READ_TOOL,
    {**_WRITE_TOOL, "cache_control": {"type": "ephemeral"}},
]

_DEFAULT_SYSTEM: list[dict] = [
    {
        "type": "text",
        "text": (
            "You are Claude Code, Anthropic's official CLI for Claude. "
            "You are an expert software engineer helping the user with programming tasks."
        ),
        "cache_control": {"type": "ephemeral"},
    }
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A tool_use block extracted from an assistant streaming response."""

    id: str
    name: str
    input: dict


@dataclass
class Turn:
    """Result of one request/response cycle with the gateway.

    Attributes:
        text: Assembled text content from all text blocks.
        tool_calls: List of tool_use blocks the assistant emitted.
        stop_reason: "end_turn", "tool_use", or "max_tokens".
        raw_events: All parsed SSE event dicts, for fine-grained assertions.
    """

    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    raw_events: list[dict] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def succeeded(self) -> bool:
        return self.stop_reason in ("end_turn", "stop") and not self.has_tool_calls


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class ClaudeCodeSimulator:
    """Simulates a Claude Code client making streaming requests through the gateway.

    Maintains conversation history across turns, building multi-turn message
    arrays with text, tool_use, and tool_result blocks — exactly as Claude Code
    does in practice.

    Each `send()` or `continue_with_tool_result()` call:
      1. Appends the appropriate message(s) to history
      2. Sends a full streaming POST /v1/messages request
      3. Parses the SSE response
      4. Appends the assistant message to history
      5. Returns a Turn with text, tool_calls, stop_reason, and raw events
    """

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        model: str = "claude-haiku-4-5",
        tools: list[dict] | None = None,
        system: list[dict] | None = None,
        timeout: float = 30.0,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._tools = tools if tools is not None else DEFAULT_TOOLS
        self._system = system if system is not None else _DEFAULT_SYSTEM
        self._timeout = timeout
        self._messages: list[dict] = []

    @property
    def messages(self) -> list[dict]:
        """Current conversation history (copy — do not mutate)."""
        return list(self._messages)

    async def send(self, message: str) -> Turn:
        """Start or continue a conversation with a user message.

        Args:
            message: The user's text message.

        Returns:
            Turn with the assistant's response (text and/or tool calls).
        """
        self._messages.append({"role": "user", "content": message})
        return await self._request()

    async def continue_with_tool_result(self, tool_use_id: str, content: str) -> Turn:
        """Provide a single tool result and get the next assistant response.

        Args:
            tool_use_id: The id from the ToolCall this result answers.
            content: The tool's output as a string.

        Returns:
            Turn with the assistant's next response.
        """
        return await self.continue_with_tool_results([(tool_use_id, content)])

    async def continue_with_tool_results(self, results: list[tuple[str, str]]) -> Turn:
        """Provide results for multiple tool calls and get the next response.

        Args:
            results: List of (tool_use_id, content) pairs.

        Returns:
            Turn with the assistant's next response.
        """
        tool_result_blocks = [
            {"type": "tool_result", "tool_use_id": tid, "content": content} for tid, content in results
        ]
        self._messages.append({"role": "user", "content": tool_result_blocks})
        return await self._request()

    async def _request(self) -> Turn:
        """Send one streaming request and parse the SSE response into a Turn."""
        payload = {
            "model": self._model,
            "max_tokens": 1024,
            "stream": True,
            "system": self._system,
            "tools": self._tools,
            "messages": self._messages,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            # Prompt caching beta — real Claude Code always sends this
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

        raw_events: list[dict] = []
        # index → accumulated text
        text_blocks: dict[int, str] = {}
        # index → {id, name, input_parts: list[str]}
        tool_blocks: dict[int, dict] = {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._gateway_url}/v1/messages",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    raw_events.append(event)
                    etype = event.get("type")

                    if etype == "content_block_start":
                        idx = event["index"]
                        cb = event.get("content_block", {})
                        if cb.get("type") == "text":
                            text_blocks[idx] = cb.get("text", "")
                        elif cb.get("type") == "tool_use":
                            tool_blocks[idx] = {
                                "id": cb["id"],
                                "name": cb["name"],
                                "input_parts": [],
                            }

                    elif etype == "content_block_delta":
                        idx = event["index"]
                        delta = event.get("delta", {})
                        dtype = delta.get("type")
                        if dtype == "text_delta" and idx in text_blocks:
                            text_blocks[idx] += delta.get("text", "")
                        elif dtype == "input_json_delta" and idx in tool_blocks:
                            tool_blocks[idx]["input_parts"].append(delta.get("partial_json", ""))

        # Assemble text from all text blocks in order
        text = "".join(text_blocks[i] for i in sorted(text_blocks))

        # Assemble tool calls and build the assistant content list for history
        tool_calls: list[ToolCall] = []
        assistant_content: list[dict] = []
        if text:
            assistant_content.append({"type": "text", "text": text})
        for idx in sorted(tool_blocks):
            tb = tool_blocks[idx]
            raw_input = "".join(tb["input_parts"])
            parsed_input = json.loads(raw_input) if raw_input else {}
            tool_calls.append(ToolCall(id=tb["id"], name=tb["name"], input=parsed_input))
            assistant_content.append({"type": "tool_use", "id": tb["id"], "name": tb["name"], "input": parsed_input})

        # Extract stop_reason from the message_delta event
        stop_reason = "end_turn"
        for ev in raw_events:
            if ev.get("type") == "message_delta":
                stop_reason = ev.get("delta", {}).get("stop_reason") or "end_turn"

        # Append assistant response to history so the next turn picks it up
        self._messages.append({"role": "assistant", "content": assistant_content})

        return Turn(text=text, tool_calls=tool_calls, stop_reason=stop_reason, raw_events=raw_events)
