#!/usr/bin/env python3
"""Extract replay callback examples for analysis."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

LogMaps = Dict[str, Dict[str, Any]]


def _parse_log(path: Path) -> tuple[LogMaps, LogMaps, LogMaps]:
    pre: LogMaps = {}
    streams: Dict[str, List[Dict[str, Any]]] = {}
    results: LogMaps = {}
    current_stream: List[Dict[str, Any]] = []

    with path.open() as handle:
        for line in handle:
            entry = json.loads(line)
            hook = entry["hook"]

            if hook == "log_pre_api_call":
                call_id = entry["kwargs"].get("litellm_call_id")
                if call_id:
                    pre[call_id] = entry
            elif hook == "async_post_call_streaming_iterator_hook":
                current_stream.append(entry)
            elif hook in {"async_log_success_event", "async_log_failure_event"}:
                call_id = entry["kwargs"].get("litellm_call_id")
                if call_id:
                    streams[call_id] = list(current_stream)
                    results[call_id] = entry
                current_stream = []

    return pre, streams, results


def _extract_user_messages(pre_entry: Dict[str, Any]) -> List[str]:
    messages = []
    for msg in pre_entry["kwargs"].get("messages", []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            joined = "\n".join(part for part in text_parts if part)
        elif isinstance(content, str):
            joined = content
        else:
            joined = ""
        if joined:
            messages.append(joined)
    return messages


def _summarize_request(pre_entry: Dict[str, Any]) -> Dict[str, Any]:
    kwargs = pre_entry["kwargs"]
    tools = kwargs.get("tools") or []
    tool_names = []
    for tool in tools:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict):
                name = function.get("name")
                if name:
                    tool_names.append(name)
            name = tool.get("name")
            if name:
                tool_names.append(name)
    return {
        "model": kwargs.get("model"),
        "temperature": kwargs.get("temperature"),
        "stream": kwargs.get("stream"),
        "tool_names": tool_names,
        "user_messages": _extract_user_messages(pre_entry),
    }


def _summarize_codex_stream(stream_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    text_chunks: List[Dict[str, Any]] = []
    tool_calls: Dict[int, Dict[str, Any]] = {}

    for idx, entry in enumerate(stream_entries):
        resp = entry.get("response_obj")
        if not isinstance(resp, dict):
            continue
        choice = (resp.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}

        content = delta.get("content")
        if content:
            text_chunks.append(
                {
                    "chunk_index": idx,
                    "text": content,
                }
            )

        for tool_call in delta.get("tool_calls") or []:
            index = tool_call.get("index", 0)
            store = tool_calls.setdefault(
                index,
                {
                    "name": tool_call.get("function", {}).get("name"),
                    "arguments": "",
                    "fragments": [],
                },
            )
            fn = tool_call.get("function", {})
            name = fn.get("name")
            if name:
                store["name"] = name
            fragment = fn.get("arguments", "")
            if fragment:
                store["arguments"] += fragment
                store["fragments"].append(
                    {
                        "chunk_index": idx,
                        "text": fragment,
                    }
                )

    tool_summary = []
    for index, info in sorted(tool_calls.items()):
        fragments = [
            {
                "chunk_index": frag["chunk_index"],
                "preview": frag["text"][:100],
            }
            for frag in info["fragments"]
        ]
        tool_summary.append(
            {
                "index": index,
                "name": info["name"],
                "arguments": info["arguments"],
                "fragment_previews": fragments,
            }
        )

    text_previews = [
        {
            "chunk_index": chunk["chunk_index"],
            "preview": chunk["text"][:100],
        }
        for chunk in text_chunks
    ]

    return {
        "text_chunk_previews": text_previews,
        "tool_calls": tool_summary,
    }


def _parse_sse(payload: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    segments = [seg for seg in payload.split("\n\n") if seg.strip()]
    for segment in segments:
        event_type = None
        data_json = None
        for line in segment.splitlines():
            stripped = line.strip()
            if stripped.startswith("event:"):
                event_type = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("data:"):
                data_json = stripped.split(":", 1)[1].strip()
        if data_json:
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError:
                continue
            events.append({"event": event_type, "data": data})
    return events


def _summarize_claude_stream(stream_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    text_fragments: List[Dict[str, Any]] = []
    tool_inputs: Dict[int, Dict[str, Any]] = {}

    for chunk_index, entry in enumerate(stream_entries):
        raw = entry.get("response_obj")
        if not isinstance(raw, str):
            continue
        payload_bytes = ast.literal_eval(raw)
        payload = payload_bytes.decode("utf-8")
        for event in _parse_sse(payload):
            evt_type = event["event"]
            data = event["data"]
            if evt_type == "content_block_start" and data.get("content_block", {}).get("type") == "tool_use":
                idx = data["index"]
                block = data["content_block"]
                tool_inputs[idx] = {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "partial": "",
                    "fragments": [],
                }
            elif evt_type == "content_block_delta":
                delta = data.get("delta", {})
                idx = data.get("index")
                if delta.get("type") == "text_delta":
                    fragment = delta.get("text", "")
                    if fragment:
                        text_fragments.append(
                            {
                                "chunk_index": chunk_index,
                                "text": fragment,
                            }
                        )
                elif delta.get("type") == "input_json_delta" and idx in tool_inputs:
                    fragment = delta.get("partial_json", "")
                    if fragment:
                        block = tool_inputs[idx]
                        block["partial"] += fragment
                        block["fragments"].append(
                            {
                                "chunk_index": chunk_index,
                                "text": fragment,
                            }
                        )

    tool_summary = []
    for index, info in sorted(tool_inputs.items()):
        try:
            input_json = json.loads(info["partial"])
        except json.JSONDecodeError:
            input_json = info["partial"]
        fragments = [
            {
                "chunk_index": frag["chunk_index"],
                "preview": frag["text"][:100],
            }
            for frag in info["fragments"]
        ]
        tool_summary.append(
            {
                "index": index,
                "name": info.get("name"),
                "id": info.get("id"),
                "input": input_json,
                "fragment_previews": fragments,
            }
        )

    text_previews = [
        {
            "chunk_index": frag["chunk_index"],
            "preview": frag["text"][:100],
        }
        for frag in text_fragments
    ]

    return {
        "text_chunk_previews": text_previews,
        "tool_calls": tool_summary,
    }


def _write_summary(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(summary, handle, indent=2)


def main() -> None:
    base = Path("dev/litellm_replay_logs")

    claude_path = base / "replay_logs_export-claudecode-20251008T030640Z.jsonl"
    codex_path = base / "replay_logs_export-codex-20251008T031142Z.jsonl"

    claude_pre, claude_streams, claude_results = _parse_log(claude_path)
    codex_pre, codex_streams, codex_results = _parse_log(codex_path)

    claude_call = next(
        cid
        for cid, entry in claude_results.items()
        if isinstance(entry.get("response_obj"), dict)
        and any(
            "python_for_cpp_devs.py" in (call.get("function", {}).get("arguments", ""))
            for call in ((entry["response_obj"].get("choices") or [{}])[0].get("message", {}).get("tool_calls") or [])
        )
    )

    codex_call = next(
        cid
        for cid, entry in codex_results.items()
        if isinstance(entry.get("response_obj"), dict)
        and entry["response_obj"].get("model", "").startswith("gpt")
        and entry["response_obj"].get("choices", [{}])[0].get("message", {}).get("tool_calls")
    )

    claude_summary = {
        "provider": "claude-code",
        "call_id": claude_call,
        "request": _summarize_request(claude_pre[claude_call]),
        "stream": _summarize_claude_stream(claude_streams[claude_call]),
        "final_message": claude_results[claude_call]["response_obj"]["choices"][0]["message"],
    }

    codex_summary = {
        "provider": "codex",
        "call_id": codex_call,
        "request": _summarize_request(codex_pre[codex_call]),
        "stream": _summarize_codex_stream(codex_streams[codex_call]),
        "final_message": codex_results[codex_call]["response_obj"]["choices"][0]["message"],
    }

    _write_summary(base / "claude_example.json", claude_summary)
    _write_summary(base / "codex_example.json", codex_summary)


if __name__ == "__main__":
    main()
