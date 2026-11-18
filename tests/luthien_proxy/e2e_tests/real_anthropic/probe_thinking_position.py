"""Probe the real Anthropic API: do thinking blocks stay valid after SimpleLLMPolicy edits a turn?

Follow-up to review of #827. Two questions:

1. What does the API accept when a signed thinking block changes position, loses a
   neighbour, or loses the tool_use that followed it? (`raw_*` cases: a real response
   is edited by hand and replayed on the next turn.)
2. Does a conversation that goes through SimpleLLMPolicy keep working? (`policy_*`
   cases: every upstream response is passed through the policy with a stub judge, the
   client history is built from the policy output, and the next two turns are sent.)

This script is NOT a pytest. Run it from the repo root:

    ANTHROPIC_API_KEY=... python -m tests.luthien_proxy.e2e_tests.real_anthropic.probe_thinking_position run

Models with preserved thinking are sent `thinking.block_binding.prefix_mismatch_behavior`
(beta `thinking-binding-controls-2026-08-01`), which turns the conversation check on for
any account and lists every dropped block in `input_transformations`. A case passes when
each replay is HTTP 200 and nothing is dropped for `prefix_binding_mismatch`.

`transform` reads {"events", "judge_thinking", "drop"} on stdin and prints the policy
output without touching the network, so `run --transform-cmd` can keep the policy code
in a separate process from the one that holds the API key.

Results recorded 2026-11-18 (effort medium, 3 or 4 samples per model):

Shape of the first response, 43 samples over claude-fable-5-2, claude-opus-5-5,
claude-sonnet-5-5, claude-haiku-5-5, claude-opus-4-8 and claude-haiku-4-5, with
`display` summarized and updates: always [thinking, text, tool, tool, tool], or the
same without the thinking block (claude-sonnet-5-5). No response had more than one
reasoning block, and none had a reasoning block after a tool_use.

claude-fable-5-2, claude-opus-5-5, claude-haiku-5-5 (conversation check on):

    [PASS 200] raw_unchanged                              [thinking, text, tool, tool, tool]
    [DROPPED]  raw_first_reasoning_moved_next_to_tools    [text, thinking, tool, tool, tool]
    [DROPPED]  raw_first_reasoning_moved_after_last_tool  [text, tool, tool, tool, thinking]
    [PASS 200] raw_all_reasoning_removed_tools_kept       [text, tool, tool, tool]
    [PASS 200] raw_marker_text_before_tools               [thinking, text, marker, tool, tool, tool]
    [DROPPED]  control_user_turn_edited                   [thinking, text, tool, tool, tool]
    [PASS 200] policy_judge_thinking_off                  both follow-up turns, stream and non_stream
    [PASS 200] policy_all_pass                            both follow-up turns, stream and non_stream
    [PASS 200] policy_first_thinking_dropped              both follow-up turns, stream and non_stream

(`raw_first_reasoning_moved_after_last_tool` was not run on claude-fable-5-2.) DROPPED is
`prefix_binding_mismatch`. On claude-opus-5-5 the same request without `block_binding`
came back as a 400: "Invalid `signature` in `thinking` block. The block is bound to a
different conversation. ... This request has content before this block that was not
present when it was created".

claude-haiku-4-5 (no conversation check): every case is 200.

Conclusion: a signed reasoning block is bound to the content before it, so it can be
kept where upstream put it or removed, but not moved. The cases that need two reasoning
blocks in one message could not run, because the API never sent that shape.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
BINDING_BETA = "thinking-binding-controls-2026-08-01"
INTERLEAVED_BETA = "interleaved-thinking-2025-05-14"
REASONING = ("thinking", "redacted_thinking")

PRESERVED = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2", "claude-mythos-5-2")
ADAPTIVE = (*PRESERVED, "claude-opus-4-8")
DEFAULT_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-4-8", "claude-haiku-4-5")

SYSTEM = "You are an ops assistant. Before every tool call say in one short sentence what you are about to do."
QUESTION = (
    "There are three config files: a.conf, b.conf and c.conf. Each has one line `port=<number>`. "
    "Read all three with read_file in this turn, then tell me which file has the highest port. "
    "Work out your plan before the first call."
)
FOLLOW_UP = "Now read d.conf as well and tell me if the answer changes."
FILES = {"a.conf": "port=8080", "b.conf": "port=9443", "c.conf": "port=3000", "d.conf": "port=9999"}
TOOLS = [
    {
        "name": "read_file",
        "description": "Read a text file and return its content",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
]
MARKER = "[1 tool call was blocked by policy]"
EFFORT = os.environ.get("PROBE_EFFORT", "high")
DISPLAY = os.environ.get("PROBE_DISPLAY", "summarized")
UPDATES_BETA = "thinking-display-updates-2026-08-18"


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    path = os.environ.get("ANTHROPIC_API_KEY_FILE", "")
    if not key and path:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            key = handle.read().strip()
    if not key:
        sys.exit("set ANTHROPIC_API_KEY or ANTHROPIC_API_KEY_FILE")
    return key


def _payload(model: str, messages: list[dict]) -> tuple[dict, list[str]]:
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "stream": True,
        "system": SYSTEM,
        "tools": TOOLS,
        "messages": messages,
    }
    betas: list[str] = []
    if model in ADAPTIVE:
        thinking: dict[str, Any] = {"type": "adaptive", "display": DISPLAY}
        if DISPLAY == "updates":
            betas.append(UPDATES_BETA)
        if model in PRESERVED:
            thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            betas.append(BINDING_BETA)
        body["thinking"] = thinking
        body["output_config"] = {"effort": EFFORT}
    else:
        body["thinking"] = {"type": "enabled", "budget_tokens": 2000}
        betas.append(INTERLEAVED_BETA)
    return body, betas


def call(model: str, messages: list[dict]) -> dict:
    body, betas = _payload(model, messages)
    headers = {
        "content-type": "application/json",
        "x-api-key": _api_key(),
        "anthropic-version": "2023-06-01",
    }
    if betas:
        headers["anthropic-beta"] = ",".join(betas)
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST", headers=headers)
    events: list[dict] = []
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8", "replace")
        try:
            text = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        return {"status": err.code, "error": text, "request_id": err.headers.get("request-id")}
    stream_error = next((e for e in events if e.get("type") == "error"), None)
    if stream_error:
        return {"status": 0, "error": json.dumps(stream_error.get("error"))}
    start = next((e["message"] for e in events if e.get("type") == "message_start"), {})
    message = assemble(events)
    return {
        "status": 200,
        "events": events,
        "content": message["content"],
        "stop_reason": message["stop_reason"],
        "dropped": start.get("input_transformations"),
    }


def assemble(events: list[dict]) -> dict:
    blocks: dict[int, dict] = {}
    partial: dict[int, str] = {}
    stop_reason = None
    for event in events:
        kind = event.get("type")
        if kind == "content_block_start":
            blocks[event["index"]] = copy.deepcopy(event["content_block"])
            partial[event["index"]] = ""
        elif kind == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            if delta["type"] == "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = block.get("signature", "") + delta["signature"]
            elif delta["type"] == "input_json_delta":
                partial[event["index"]] += delta["partial_json"]
        elif kind == "content_block_stop":
            block = blocks[event["index"]]
            if block.get("type") == "tool_use":
                block["input"] = json.loads(partial[event["index"]] or "{}")
        elif kind == "message_delta":
            stop_reason = event["delta"].get("stop_reason") or stop_reason
    return {"content": [blocks[i] for i in sorted(blocks)], "stop_reason": stop_reason}


def shape(content: list[dict]) -> str:
    return "[" + ", ".join(block.get("type", "?") for block in content) + "]"


def results_for(content: list[dict]) -> list[dict]:
    return [
        {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": FILES.get(str(block.get("input", {}).get("path", "")), "no such file"),
        }
        for block in content
        if block.get("type") == "tool_use"
    ]


def next_user(content: list[dict], fallback: str) -> dict:
    return {"role": "user", "content": results_for(content) or [{"type": "text", "text": fallback}]}


def verdict(reply: dict) -> str:
    if reply["status"] != 200:
        return f"REJECT {reply['status']}: {reply.get('error', '')[:230]}"
    dropped = [
        f"{item.get('path')} {item.get('reason')}"
        for item in reply.get("dropped") or []
        if item.get("type") == "thinking_dropped"
    ]
    if dropped:
        return "DROPPED " + "; ".join(dropped)
    checked = "" if reply.get("dropped") is not None else " (no conversation check on this model)"
    return f"PASS 200{checked}"


# ---------------------------------------------------------------- policy transform


def _events_as_models(events: list[dict]) -> list[Any]:
    from anthropic.types import RawMessageStreamEvent
    from pydantic import TypeAdapter

    adapter = TypeAdapter(RawMessageStreamEvent)
    return [adapter.validate_python(e) for e in events if e.get("type") not in ("ping", "error")]


async def _transform(events: list[dict], judge_thinking: bool, drop: list[str]) -> dict:
    from unittest.mock import AsyncMock, patch

    from tests.luthien_proxy.fixtures.policy_context import make_policy_context

    from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
    from luthien_proxy.policies.simple_llm_utils import JudgeAction, SimpleLLMJudgeConfig

    async def decide(descriptor: Any, previous: Any, context: Any) -> JudgeAction:
        if descriptor.type == "thinking" and descriptor.content in drop:
            return JudgeAction(action="replace", blocks=())
        return JudgeAction(action="pass")

    def policy() -> SimpleLLMPolicy:
        config = SimpleLLMJudgeConfig(
            instructions="probe", on_error="block", auth_provider="user_credentials", judge_thinking=judge_thinking
        )
        return SimpleLLMPolicy(config)

    streaming = policy()
    out: list[Any] = []
    with patch.object(streaming, "_judge_block", AsyncMock(side_effect=decide)):
        ctx = make_policy_context()
        for event in _events_as_models(events):
            out.extend(await streaming.on_anthropic_stream_event(event, ctx))
    stream = assemble([e.model_dump(mode="json", exclude_none=True) for e in out])

    upstream = assemble(events)
    plain = policy()
    with patch.object(plain, "_judge_block", AsyncMock(side_effect=decide)):
        response = await plain.on_anthropic_response(
            {"content": upstream["content"], "stop_reason": upstream["stop_reason"]},  # type: ignore[arg-type]
            make_policy_context(),
        )
    non_stream = {"content": list(response["content"]), "stop_reason": response.get("stop_reason")}
    return {"stream": stream, "non_stream": non_stream}


def transform(events: list[dict], judge_thinking: bool, drop: list[str], command: str | None) -> dict:
    request = {"events": events, "judge_thinking": judge_thinking, "drop": drop}
    if command is None:
        return asyncio.run(_transform(**request))
    done = subprocess.run(command, shell=True, input=json.dumps(request), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        sys.exit(f"transform command failed: {done.stderr[-2000:]}")
    return json.loads(done.stdout)


# ---------------------------------------------------------------- cases


def _thinking_texts(content: list[dict]) -> list[str]:
    return [str(block.get("thinking", "")) for block in content if block.get("type") == "thinking"]


def _first_tool(content: list[dict]) -> int:
    return next((i for i, block in enumerate(content) if block.get("type") == "tool_use"), len(content))


def _last_tool(content: list[dict]) -> int:
    return max((i for i, block in enumerate(content) if block.get("type") == "tool_use"), default=-1)


def raw_edits(content: list[dict]) -> dict[str, list[dict] | None]:
    reasoning = [i for i, block in enumerate(content) if block.get("type") in REASONING]
    first_tool, last_tool = _first_tool(content), _last_tool(content)
    after_first_tool = [i for i in reasoning if i > first_tool]
    trailing = [i for i in reasoning if i > last_tool >= 0]

    def without(indexes: list[int]) -> list[dict]:
        return [block for i, block in enumerate(content) if i not in indexes]

    def moved_before_first_tool(indexes: list[int]) -> list[dict]:
        rest = without(indexes)
        at = _first_tool(rest)
        return rest[:at] + [content[i] for i in indexes] + rest[at:]

    reversed_reasoning = list(content)
    for target, source in zip(reasoning, reversed(reasoning)):
        reversed_reasoning[target] = content[source]

    marker_at = _first_tool(content)
    return {
        "raw_unchanged": list(content),
        "raw_first_reasoning_moved_next_to_tools": (
            moved_before_first_tool(reasoning[:1]) if reasoning and reasoning[0] + 1 < first_tool else None
        ),
        "raw_first_reasoning_moved_after_last_tool": (
            without(reasoning[:1]) + [content[reasoning[0]]] if reasoning and last_tool >= 0 else None
        ),
        "raw_trailing_reasoning_moved_before_tools": moved_before_first_tool(trailing) if trailing else None,
        "raw_trailing_reasoning_removed": without(trailing) if trailing else None,
        "raw_reasoning_between_tools_moved_before_tools": (
            moved_before_first_tool(after_first_tool) if after_first_tool else None
        ),
        "raw_reasoning_order_reversed": reversed_reasoning if len(reasoning) > 1 else None,
        "raw_first_reasoning_removed_later_kept": without(reasoning[:1]) if len(reasoning) > 1 else None,
        "raw_last_reasoning_removed": without(reasoning[-1:]) if len(reasoning) > 1 else None,
        "raw_all_reasoning_removed_tools_kept": without(reasoning) if reasoning and last_tool >= 0 else None,
        "raw_marker_text_before_tools": (
            content[:marker_at] + [{"type": "text", "text": MARKER}] + content[marker_at:] if last_tool >= 0 else None
        ),
    }


def run_raw(model: str, first: dict, report: list[dict]) -> None:
    opening = {"role": "user", "content": QUESTION}
    for name, content in raw_edits(first["content"]).items():
        if content is None:
            report.append({"model": model, "case": name, "result": "n/a (upstream never sent this shape)"})
            continue
        reply = call(model, [opening, {"role": "assistant", "content": content}, next_user(content, FOLLOW_UP)])
        report.append({"model": model, "case": name, "sent": shape(content), "result": verdict(reply)})
    edited = {"role": "user", "content": QUESTION + " Thanks."}
    reply = call(
        model,
        [edited, {"role": "assistant", "content": first["content"]}, next_user(first["content"], FOLLOW_UP)],
    )
    report.append(
        {
            "model": model,
            "case": "control_user_turn_edited",
            "sent": shape(first["content"]),
            "result": verdict(reply),
        }
    )


def run_policy(model: str, first: dict, report: list[dict], command: str | None) -> None:
    texts = _thinking_texts(first["content"])
    plans: dict[str, tuple[bool, list[str]] | None] = {
        "policy_judge_thinking_off": (False, []),
        "policy_all_pass": (True, []),
        "policy_first_thinking_dropped": (True, texts[:1]) if texts else None,
        "policy_last_thinking_dropped": (True, texts[-1:]) if len(texts) > 1 else None,
    }
    for name, plan in plans.items():
        if plan is None:
            report.append({"model": model, "case": name, "result": "n/a (upstream never sent this shape)"})
            continue
        judge_thinking, drop = plan
        for path in ("stream", "non_stream"):
            messages: list[dict] = [{"role": "user", "content": QUESTION}]
            upstream, turns = first, []
            for turn in (1, 2):
                sent = shape(upstream["content"])
                seen = transform(upstream["events"], judge_thinking, drop if turn == 1 else [], command)[path]
                messages += [
                    {"role": "assistant", "content": seen["content"]},
                    next_user(seen["content"], FOLLOW_UP),
                ]
                upstream = call(model, messages)
                turns.append(
                    f"turn {turn + 1}: upstream {sent}, client saw {shape(seen['content'])} -> {verdict(upstream)}"
                )
                if upstream["status"] != 200:
                    break
            report.append({"model": model, "case": f"{name} ({path})", "result": " | ".join(turns)})


def run(models: list[str], samples: int, command: str | None, out: str | None, shapes_only: bool = False) -> None:
    report: list[dict] = []
    for model in models:
        shapes: dict[str, int] = {}
        first: dict | None = None
        for _ in range(samples):
            reply = call(model, [{"role": "user", "content": QUESTION}])
            if reply["status"] != 200:
                report.append({"model": model, "case": "mint", "result": verdict(reply)})
                break
            key = shape(reply["content"])
            shapes[key] = shapes.get(key, 0) + 1
            reasoning = sum(block.get("type") in REASONING for block in reply["content"])
            if first is None or reasoning > sum(block.get("type") in REASONING for block in first["content"]):
                first = reply
        report.append({"model": model, "case": "upstream_shapes", "result": json.dumps(shapes)})
        if shapes_only or first is None or _last_tool(first["content"]) < 0:
            continue
        run_raw(model, first, report)
        run_policy(model, first, report, command)
        if out:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=1)
    for row in report:
        print(f"{row['model']:<18} {row['case']:<52} {row.get('sent', ''):<60} {row['result']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("transform")
    runner = sub.add_parser("run")
    runner.add_argument("--models", default=",".join(DEFAULT_MODELS))
    runner.add_argument("--samples", type=int, default=3)
    runner.add_argument("--transform-cmd")
    runner.add_argument("--out")
    runner.add_argument("--shapes-only", action="store_true")
    args = parser.parse_args()
    if args.command == "transform":
        request = json.load(sys.stdin)
        json.dump(asyncio.run(_transform(**request)), sys.stdout)
        return
    run(args.models.split(","), args.samples, args.transform_cmd, args.out, args.shapes_only)


if __name__ == "__main__":
    main()
