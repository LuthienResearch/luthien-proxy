# ABOUTME: Normalizes Anthropic streaming payloads to OpenAI-style chunks.
# ABOUTME: Provides adapters to convert between Anthropic SSE events and OpenAI chunks.
"""Helpers to normalize streaming payloads between Anthropic and OpenAI."""

from __future__ import annotations

import json
import time
import uuid
from typing import Dict, Iterable, List, Optional


def _parse_sse_events(raw: bytes) -> List[tuple[str | None, dict]]:
    """Parse Anthropic SSE payload into `(event, data)` tuples."""
    text = raw.decode("utf-8")
    segments = [seg for seg in text.split("\n\n") if seg.strip()]
    events: List[tuple[str | None, dict]] = []
    for segment in segments:
        event_type: str | None = None
        data_lines: List[str] = []
        for line in segment.splitlines():
            stripped = line.strip()
            if stripped.startswith("event:"):
                event_type = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("data:"):
                data_lines.append(stripped.split(":", 1)[1].strip())
        if not data_lines:
            continue
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        events.append((event_type, data))
    return events


def _map_anthropic_finish(reason: Optional[str]) -> Optional[str]:
    """Map Anthropic stop reasons onto OpenAI equivalents."""
    if reason is None:
        return None
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    return mapping.get(reason, reason)


def _map_openai_finish(reason: Optional[str]) -> Optional[str]:
    """Map OpenAI finish reasons back to Anthropic codes."""
    if reason is None:
        return None
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
    }
    return mapping.get(reason, reason)


def _serialize_event(event_type: str, data: dict) -> str:
    """Render a `(event, data)` pair into an SSE-formatted string."""
    payload = dict(data)
    payload.setdefault("type", event_type)
    return f"event: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


class AnthropicToOpenAIAdapter:
    """Converts Anthropic SSE streaming events into OpenAI-style chunks."""

    def __init__(self) -> None:
        """Initialize adapter state."""
        self.model: Optional[str] = None
        self.message_id: Optional[str] = None
        self.created: int = int(time.time())
        self.tool_states: Dict[int, Dict[str, str]] = {}
        self.finish_emitted = False

    def process(self, payload: bytes) -> List[dict]:
        """Convert an Anthropic SSE payload into zero or more OpenAI chunks."""
        chunks: List[dict] = []
        for event_type, data in _parse_sse_events(payload):
            if event_type == "message_start":
                self.tool_states.clear()
                self.finish_emitted = False
                message = data.get("message", {})
                self.model = message.get("model", self.model)
                self.message_id = message.get("id", self.message_id)
                self.created = int(time.time())
                role = message.get("role")
                if role:
                    chunks.append(self._chunk({"role": role}))
            elif event_type == "content_block_start":
                block = data.get("content_block", {})
                index = data.get("index", 0)
                block_type = block.get("type")
                if block_type == "tool_use":
                    tool_state = {
                        "id": block.get("id", f"tool_{uuid.uuid4().hex}"),
                        "name": block.get("name", "tool"),
                    }
                    self.tool_states[index] = tool_state
                    chunks.append(
                        self._chunk(
                            {
                                "tool_calls": [
                                    {
                                        "id": tool_state["id"],
                                        "type": "function",
                                        "index": index,
                                        "function": {"name": tool_state["name"], "arguments": ""},
                                    }
                                ]
                            }
                        )
                    )
            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                index = data.get("index", 0)
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        chunks.append(self._chunk({"content": text}))
                elif delta_type == "input_json_delta":
                    fragment = delta.get("partial_json", "")
                    if fragment:
                        tool_state = self.tool_states.get(index)
                        if tool_state is None:
                            tool_state = {"id": f"tool_{uuid.uuid4().hex}", "name": "tool"}
                            self.tool_states[index] = tool_state
                        chunks.append(
                            self._chunk(
                                {
                                    "tool_calls": [
                                        {
                                            "id": tool_state["id"],
                                            "type": "function",
                                            "index": index,
                                            "function": {
                                                "name": tool_state["name"],
                                                "arguments": fragment,
                                            },
                                        }
                                    ]
                                }
                            )
                        )
            elif event_type == "message_delta":
                finish = _map_anthropic_finish(data.get("delta", {}).get("stop_reason"))
                if finish and not self.finish_emitted:
                    chunks.append(self._chunk({}, finish))
                    self.finish_emitted = True
            elif event_type == "message_stop":
                if not self.finish_emitted:
                    chunks.append(self._chunk({}, "stop"))
                    self.finish_emitted = True
            # ping/content_block_stop events are intentionally ignored
        return chunks

    def finalize(self) -> List[dict]:
        """Return any trailing chunks that should be emitted after the stream."""
        return []

    def _chunk(self, delta: dict, finish_reason: Optional[str] = None) -> dict:
        """Construct a single OpenAI-style chunk."""
        message_id = self.message_id or f"anthropic_{uuid.uuid4().hex}"
        model = self.model or "anthropic-unknown"
        return {
            "id": message_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                    "logprobs": None,
                }
            ],
        }


class OpenAIToAnthropicAdapter:
    """Converts OpenAI-style streaming chunks back into Anthropic SSE events."""

    def __init__(self, model: Optional[str] = None, message_id: Optional[str] = None) -> None:
        """Initialize adapter state."""
        self.model = model or "claude-sonnet"
        self.message_id = message_id or f"msg_{uuid.uuid4().hex}"
        self.started = False
        self.text_block_started = False
        self.tool_blocks: Dict[str, int] = {}
        self.next_block_index = 1  # 0 reserved for text blocks
        self.finished = False

    def process(self, chunk: dict) -> List[str]:
        """Convert an OpenAI streaming chunk into zero or more SSE events."""
        if self.finished:
            delta_peek = (chunk.get("choices") or [{}])[0].get("delta") or {}
            if delta_peek.get("role"):
                self._reset_state()
            else:
                return []
        events: List[str] = []
        chunk_model = chunk.get("model")
        if chunk_model:
            self.model = chunk_model
        choices = chunk.get("choices") or [{}]
        choice = choices[0]
        delta = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason")

        role = delta.get("role")
        if not self.started:
            events.extend(self._start_message(role))

        content_fragments = self._extract_content(delta.get("content"))
        if content_fragments and not self.text_block_started:
            events.append(
                _serialize_event(
                    "content_block_start",
                    {"index": 0, "content_block": {"type": "text", "text": ""}},
                )
            )
            self.text_block_started = True
        for fragment in content_fragments:
            events.append(
                _serialize_event(
                    "content_block_delta",
                    {"index": 0, "delta": {"type": "text_delta", "text": fragment}},
                )
            )

        for tool_event in delta.get("tool_calls") or []:
            tool_id = tool_event.get("id") or f"tool_{uuid.uuid4().hex}"
            state_index = self.tool_blocks.get(tool_id)
            if state_index is None:
                state_index = self.next_block_index
                self.next_block_index += 1
                self.tool_blocks[tool_id] = state_index
                events.append(
                    _serialize_event(
                        "content_block_start",
                        {
                            "index": state_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_event.get("function", {}).get("name", "tool"),
                                "input": {},
                            },
                        },
                    )
                )
            fragment = tool_event.get("function", {}).get("arguments", "")
            if fragment:
                events.append(
                    _serialize_event(
                        "content_block_delta",
                        {
                            "index": state_index,
                            "delta": {"type": "input_json_delta", "partial_json": fragment},
                        },
                    )
                )

        if finish_reason is not None and not self.finished:
            mapped = _map_openai_finish(finish_reason)
            events.append(
                _serialize_event(
                    "message_delta",
                    {"delta": {"stop_reason": mapped, "stop_sequence": None}},
                )
            )
            events.append(_serialize_event("message_stop", {}))
            self.finished = True
        return events

    def finalize(self) -> List[str]:
        """Emit termination events when the stream completes."""
        if self.finished:
            return []
        self.finished = True
        return [_serialize_event("message_stop", {})]

    def _start_message(self, role: Optional[str]) -> List[str]:
        """Emit initial message_start event (and optional text block)."""
        self.started = True
        payload = {
            "message": {
                "id": self.message_id,
                "model": self.model,
                "type": "message",
                "role": role or "assistant",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {},
            }
        }
        return [_serialize_event("message_start", payload)]

    @staticmethod
    def _extract_content(content: object) -> List[str]:
        """Normalize OpenAI delta content into a list of text fragments."""
        if content is None:
            return []
        if isinstance(content, str):
            return [content]
        fragments: List[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
        return fragments

    def _reset_state(self) -> None:
        """Reset adapter state for a new message."""
        self.started = False
        self.text_block_started = False
        self.tool_blocks.clear()
        self.next_block_index = 1
        self.finished = False


def anthropic_stream_to_openai(raw_events: Iterable[bytes]) -> List[dict]:
    """Convert an iterable of Anthropic SSE payloads into OpenAI chunks."""
    adapter = AnthropicToOpenAIAdapter()
    chunks: List[dict] = []
    for payload in raw_events:
        chunks.extend(adapter.process(payload))
    chunks.extend(adapter.finalize())
    return chunks


def openai_chunks_to_anthropic(chunks: Iterable[dict]) -> List[str]:
    """Convert an iterable of OpenAI chunks back into Anthropic SSE events."""
    adapter = OpenAIToAnthropicAdapter()
    events: List[str] = []
    for chunk in chunks:
        events.extend(adapter.process(chunk))
    events.extend(adapter.finalize())
    return events
