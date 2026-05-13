"""Probe the real Anthropic API for the tool_use-trailing invariant.

Empirical verification for issue #708. Anthropic rejects a 2-turn conversation
with `messages.1: tool_use ids were found without tool_result blocks
immediately after` whenever the assistant message in `messages[1]` has any
non-tool_use content following the first tool_use block.

This script is NOT a pytest. It's a documentation artifact + a one-shot probe
you can run with `ANTHROPIC_API_KEY=... python -m
tests.luthien_proxy.e2e_tests.real_anthropic.probe_tool_use_invariant` to
re-verify the invariant against the live API.

Results recorded 2026-05-13 (claude-haiku-4-5):

    [PASS 200] baseline_ok_text_then_tool             [text, tool]
    [REJECT 400] case_A_warning_after_tool            [text, tool, warning]
    [PASS 200] case_B_warning_before_tool             [warning, text, tool]
    [REJECT 400] trace_1_tool_then_text               [tool, text]
    [REJECT 400] trace_1_tool_then_text_then_warning  [tool, text, warning]
    [REJECT 400] trace_2_tool_text_tool               [tool_A, text, tool_B]
    [REJECT 400] trace_3_text_warning_tool_text       [text, warning, tool, text]
    [REJECT 400] S2_action_block_sibling              [tool, blocked_text]
    [PASS 200] parallel_tools_no_text_between         [tool_A, tool_B]

Conclusion: the first tool_use in an assistant message must be followed only
by other tool_use blocks until end-of-message.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MODEL = "claude-haiku-4-5"
ENDPOINT = "https://api.anthropic.com/v1/messages"

TOOL_A_ID = "toolu_test_a_0001"
TOOL_B_ID = "toolu_test_b_0002"

WARNING = "Safety judge unavailable, proceeding without review."

TOOLS = [
    {
        "name": "Bash",
        "description": "Run a bash command",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]


def _build_payload(assistant_content: list[dict], tool_results: list[dict]) -> dict:
    return {
        "model": MODEL,
        "max_tokens": 64,
        "tools": TOOLS,
        "messages": [
            {"role": "user", "content": "Install scrapling"},
            {"role": "assistant", "content": assistant_content},
            {"role": "user", "content": tool_results},
        ],
    }


def _post(api_key: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")[:300]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, body[:500]


def _tool_use(id_: str, command: str) -> dict:
    return {"type": "tool_use", "id": id_, "name": "Bash", "input": {"command": command}}


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool_result(id_: str, output: str) -> dict:
    return {"type": "tool_result", "tool_use_id": id_, "content": output}


CASES = {
    "baseline_ok_text_then_tool": (
        [_text("I'll install Scrapling."), _tool_use(TOOL_A_ID, "pip install scrapling")],
        [_tool_result(TOOL_A_ID, "ok")],
        "200 (control: known-good shape)",
    ),
    "case_A_warning_after_tool": (
        [_text("I'll install Scrapling."), _tool_use(TOOL_A_ID, "pip install scrapling"), _text(WARNING)],
        [_tool_result(TOOL_A_ID, "ok")],
        "400 (original #708 bug)",
    ),
    "case_B_warning_before_tool": (
        [_text(WARNING), _text("I'll install Scrapling."), _tool_use(TOOL_A_ID, "pip install scrapling")],
        [_tool_result(TOOL_A_ID, "ok")],
        "200 (PR's fixed shape)",
    ),
    "trace_1_tool_then_text": (
        [_tool_use(TOOL_A_ID, "pip install scrapling"), _text("Done.")],
        [_tool_result(TOOL_A_ID, "ok")],
        "400 (upstream text after tool_use)",
    ),
    "trace_1_tool_then_text_then_warning": (
        [_tool_use(TOOL_A_ID, "pip install scrapling"), _text("Done."), _text(WARNING)],
        [_tool_result(TOOL_A_ID, "ok")],
        "400 (any text after tool_use 400s)",
    ),
    "trace_2_tool_text_tool": (
        [_tool_use(TOOL_A_ID, "pip install scrapling"), _text(WARNING), _tool_use(TOOL_B_ID, "pip install requests")],
        [_tool_result(TOOL_A_ID, "ok"), _tool_result(TOOL_B_ID, "ok")],
        "400 (text between parallel tool_uses)",
    ),
    "trace_3_text_warning_tool_text": (
        [_text("Preamble"), _text(WARNING), _tool_use(TOOL_A_ID, "pip install scrapling"), _text("Trailing")],
        [_tool_result(TOOL_A_ID, "ok")],
        "400 (text after tool_use, even with preamble)",
    ),
    "S2_action_block_sibling": (
        [_tool_use(TOOL_A_ID, "pip install scrapling"), _text("[Tool call Bash was blocked by policy]")],
        [_tool_result(TOOL_A_ID, "ok")],
        "400 (action=block emitting marker after a prior tool_use)",
    ),
    "parallel_tools_no_text_between": (
        [_tool_use(TOOL_A_ID, "pip install scrapling"), _tool_use(TOOL_B_ID, "pip install requests")],
        [_tool_result(TOOL_A_ID, "ok"), _tool_result(TOOL_B_ID, "ok")],
        "200 (parallel tools with no interleaving)",
    ),
}


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2
    print(f"Probing {ENDPOINT} with {MODEL}\n")
    results: list[tuple[str, int, str]] = []
    for name, (assistant, tool_results, expectation) in CASES.items():
        status, body = _post(api_key, _build_payload(assistant, tool_results))
        verdict = "PASS" if status == 200 else "REJECT"
        print(f"[{verdict} {status}] {name}")
        print(f"   expect: {expectation}")
        if status != 200:
            try:
                err = json.loads(body).get("error", {}).get("message", "")
                print(f"   error: {err[:240]}")
            except Exception:
                print(f"   body: {body[:200]}")
        print()
        results.append((name, status, body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
