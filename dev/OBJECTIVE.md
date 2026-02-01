Objective: Fix Codex tool-call sequencing error (missing tool result messages after tool_calls).

Background
- Codex emits tool_calls (e.g., for MCP server discovery), but the proxy sometimes fails to return the required tool result messages.
- This triggers: “An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'.”
- This now reproduces even for a simple `hello` prompt in Codex.

Acceptance
- Codex no longer errors with missing tool result messages during normal prompts.
- Tool-call paths either return tool result messages or are blocked safely with a clear error.
- Regression test added for tool-call sequencing.

Repro (from session)
1. Run gateway on branch `codex-developer-role` (or any branch with Codex launcher).
2. `./scripts/launch_codex.sh`
3. Prompt: `trello mcp?` or even `hello`
4. Observe error: “An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'."

Plan (TDD)
1. Add a failing unit/integration test for tool-call sequencing in the chat SSE path.
2. Capture gateway logs for a failing request.
3. Identify where tool result messages are dropped or not emitted.
4. Fix policy/formatter/tool plumbing to ensure tool responses are always sent or tool calls are suppressed.
5. Add regression test + run targeted tests.
