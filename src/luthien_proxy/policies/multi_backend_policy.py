"""MultiBackendPolicy - fan out each request to multiple Anthropic models in parallel.

Sends the same request to every configured model using the caller's credential
(passthrough auth). In streaming mode, the first model to start emitting content
is streamed live; the others buffer and are flushed sequentially in arrival
order once the current model finishes. Each model's section is labeled with a
header. In non-streaming mode, all responses are concatenated in config order.

Tool-use blocks are rendered as labeled text rather than forwarded as real
tool_use content, so the aggregated response never contains executable tool
calls from multiple models simultaneously.

Example config:

    policy:
      class: "luthien_proxy.policies.multi_backend_policy:MultiBackendPolicy"
      config:
        models:
          - claude-opus-4-7
          - claude-sonnet-4-6
          - claude-haiku-4-5-20251001
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    Message,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    Usage,
)
from anthropic.types.raw_message_delta_event import Delta as RawMessageDelta
from pydantic import BaseModel, Field

from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    build_usage,
)
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicPolicyEmission

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext


logger = logging.getLogger(__name__)


# =============================================================================
# Config
# =============================================================================


class MultiBackendConfig(BaseModel):
    """Configuration for :class:`MultiBackendPolicy`."""

    models: list[str] = Field(
        min_length=1,
        description=(
            "Anthropic model names to fan out to. The first entry is the primary "
            "backend call (driven by the executor); others are issued in parallel "
            "with the caller's credential."
        ),
    )
    stagger_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Delay (seconds) between launching successive fan-out calls. "
            "``models[i]`` (for i >= 1) waits ``i * stagger_seconds`` before "
            "hitting the backend. Use this to avoid rate limits that kick in "
            "when N identical requests fire simultaneously."
        ),
    )


# =============================================================================
# Per-request state
# =============================================================================


@dataclass
class _ModelStream:
    """Collects events from a single model's stream.

    Events are pushed onto ``queue`` by the producer (either the primary's
    ``on_anthropic_stream_event`` hook or an extra model's background task).
    A terminal ``None`` is pushed when the stream ends. ``announced`` flips
    true the first time the producer sees a ``content_block_start`` (or when
    the stream ends without emitting one), at which point the producer adds
    this model's index to ``arrivals``.
    """

    idx: int
    name: str
    queue: asyncio.Queue  # entries are MessageStreamEvent | None
    announced: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class _MultiBackendState:
    """Per-request state for :class:`MultiBackendPolicy`."""

    streams: list[_ModelStream]
    arrivals: asyncio.Queue  # pushes int (model idx) in arrival order
    output_queue: asyncio.Queue  # entries are MessageStreamEvent | None
    extra_tasks: list[asyncio.Task] = field(default_factory=list)
    coordinator_task: asyncio.Task | None = None
    # Non-streaming fan-out tasks (parallel ``client.complete`` calls).
    complete_tasks: list[asyncio.Task] = field(default_factory=list)


# =============================================================================
# Policy
# =============================================================================


class MultiBackendPolicy(BasePolicy, AnthropicHookPolicy):
    """Fan out each request to multiple Anthropic models and aggregate the responses."""

    def __init__(self, config: MultiBackendConfig | dict | None = None) -> None:
        """Initialize the policy from a config model, dict, or defaults."""
        self.config = self._init_config(config, MultiBackendConfig)
        # Immutable tuple view for freeze_configured_state compliance.
        self._models: tuple[str, ...] = tuple(self.config.models)

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name used in logs and UI."""
        return "MultiBackend"

    # -------------------------------------------------------------------------
    # Hooks
    # -------------------------------------------------------------------------

    async def on_anthropic_request(
        self,
        request: AnthropicRequest,
        context: "PolicyContext",
    ) -> AnthropicRequest:
        """Spawn parallel fan-out tasks and point the primary call at models[0]."""
        credential = context.user_credential
        if credential is None:
            raise CredentialError(
                "MultiBackendPolicy requires a forwarding user credential. "
                "Configure the gateway in passthrough auth mode so the "
                "caller's credential is available to the policy."
            )

        state = context.get_request_state(self, _MultiBackendState, self._new_state)
        is_streaming = bool(request.get("stream", False))

        extra_headers = self._extra_headers_from_context(context)

        if is_streaming:
            await self._launch_streaming(state, request, credential, extra_headers)
        else:
            await self._launch_complete(state, request, credential, extra_headers)

        # Primary call (driven by the executor) uses models[0]. Sanitize per
        # the primary model's capability profile so the executor-driven call
        # also meets model-specific requirements.
        return _request_for_model(request, self._models[0])

    async def on_anthropic_stream_event(
        self,
        event: MessageStreamEvent,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Feed the primary event into the coordinator and drain any ready output."""
        state = context.get_request_state(self, _MultiBackendState, self._new_state)
        primary = state.streams[0]
        await self._feed_stream_event(primary, event, state)
        # Yield control so the coordinator can process what we just fed in.
        await asyncio.sleep(0)
        return self._drain_output_nowait(state)

    async def on_anthropic_stream_complete(
        self,
        context: "PolicyContext",
    ) -> list[AnthropicPolicyEmission]:
        """Signal end of primary, wait for coordinator, drain remaining output."""
        state = context.get_request_state(self, _MultiBackendState, self._new_state)
        primary = state.streams[0]
        await self._mark_stream_done(primary, state)

        # Wait for all background work (coordinator + extra producers).
        if state.extra_tasks:
            await asyncio.gather(*state.extra_tasks, return_exceptions=True)
        if state.coordinator_task is not None:
            await state.coordinator_task

        return cast("list[AnthropicPolicyEmission]", self._drain_output_nowait(state))

    async def on_anthropic_response(
        self,
        response: AnthropicResponse,
        context: "PolicyContext",
    ) -> AnthropicResponse:
        """Aggregate the primary response with parallel non-streaming fan-out calls."""
        state = context.get_request_state(self, _MultiBackendState, self._new_state)

        # The executor already gave us models[0]'s response via ``response``.
        # Extras were kicked off in on_anthropic_request.
        extra_results: list[AnthropicResponse | BaseException] = []
        if state.complete_tasks:
            extra_results = await asyncio.gather(*state.complete_tasks, return_exceptions=True)

        all_results: list[AnthropicResponse | BaseException] = [response, *extra_results]
        return self._build_aggregated_response(all_results)

    # -------------------------------------------------------------------------
    # Streaming: launch + coordinator
    # -------------------------------------------------------------------------

    async def _launch_streaming(
        self,
        state: _MultiBackendState,
        request: AnthropicRequest,
        credential: Credential,
        extra_headers: dict[str, str] | None,
    ) -> None:
        """Start extra streaming producers and the merge coordinator."""
        # Extra producers (all but the primary) each run a background task.
        for idx in range(1, len(self._models)):
            extra_request = _request_for_model(request, self._models[idx])
            delay = idx * self.config.stagger_seconds
            task = asyncio.create_task(
                self._run_extra_stream(state.streams[idx], state, extra_request, credential, extra_headers, delay),
                name=f"multi_backend_extra_stream[{idx}]",
            )
            state.extra_tasks.append(task)

        state.coordinator_task = asyncio.create_task(
            self._coordinator(state),
            name="multi_backend_coordinator",
        )

    async def _run_extra_stream(
        self,
        stream: _ModelStream,
        state: _MultiBackendState,
        request: AnthropicRequest,
        credential: Credential,
        extra_headers: dict[str, str] | None,
        delay_seconds: float,
    ) -> None:
        """Run a single extra streaming call and forward its events into the queue."""
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            client = self._client_for_credential(credential)
            try:
                async for event in client.stream(request, extra_headers=extra_headers):
                    # RawMessageStreamEvent is a subset of MessageStreamEvent;
                    # the cast bridges Pyright's strict union check (matching
                    # the identical cast in pipeline/anthropic_processor.py).
                    await self._feed_stream_event(stream, cast(MessageStreamEvent, event), state)
            finally:
                await client.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MultiBackendPolicy extra stream for %s failed: %s",
                stream.name,
                repr(e),
            )
            stream.error = self._format_error(e)
        finally:
            await self._mark_stream_done(stream, state)

    async def _feed_stream_event(
        self,
        stream: _ModelStream,
        event: MessageStreamEvent,
        state: _MultiBackendState,
    ) -> None:
        """Classify one event and either capture metadata or enqueue it."""
        etype = getattr(event, "type", None)
        if etype == "message_start":
            msg = getattr(event, "message", None)
            usage = getattr(msg, "usage", None) if msg is not None else None
            if usage is not None:
                stream.input_tokens = getattr(usage, "input_tokens", 0) or 0
            return
        if etype == "message_delta":
            usage = getattr(event, "usage", None)
            if usage is not None:
                stream.output_tokens = getattr(usage, "output_tokens", 0) or 0
            return
        if etype == "message_stop":
            # Framing event — the coordinator synthesizes its own. Ignore.
            return

        # Any content_block_* event signals the model is producing output.
        if not stream.announced:
            stream.announced = True
            await state.arrivals.put(stream.idx)
        await stream.queue.put(event)

    async def _mark_stream_done(self, stream: _ModelStream, state: _MultiBackendState) -> None:
        """Push the end-sentinel and ensure the model has been announced."""
        if not stream.announced:
            stream.announced = True
            await state.arrivals.put(stream.idx)
        await stream.queue.put(None)

    async def _coordinator(self, state: _MultiBackendState) -> None:
        """Merge per-model streams into a single output stream in arrival order."""
        try:
            msg_id = f"msg_multi_{uuid.uuid4().hex[:24]}"
            model_label = self._aggregated_model_label()

            await state.output_queue.put(
                RawMessageStartEvent.model_construct(
                    type="message_start",
                    message=Message.model_construct(
                        id=msg_id,
                        type="message",
                        role="assistant",
                        model=model_label,
                        content=[],
                        stop_reason=None,
                        stop_sequence=None,
                        usage=Usage(input_tokens=0, output_tokens=0),
                    ),
                ),
            )

            out_idx = 0
            total_out_tokens = 0

            for _ in range(len(self._models)):
                model_idx = await state.arrivals.get()
                stream = state.streams[model_idx]

                out_idx = await self._emit_header_block(state.output_queue, out_idx, stream.name)
                out_idx = await self._drain_model_stream(state.output_queue, stream, out_idx)

                total_out_tokens += stream.output_tokens
                if stream.error:
                    out_idx = await self._emit_error_block(state.output_queue, out_idx, stream.error)

            await state.output_queue.put(
                RawMessageDeltaEvent.model_construct(
                    type="message_delta",
                    delta=RawMessageDelta.model_construct(stop_reason="end_turn", stop_sequence=None),
                    usage=Usage(input_tokens=0, output_tokens=total_out_tokens),
                ),
            )
            await state.output_queue.put(RawMessageStopEvent(type="message_stop"))
        except Exception as e:  # noqa: BLE001
            logger.exception("MultiBackendPolicy coordinator failed: %s", repr(e))
        finally:
            await state.output_queue.put(None)

    async def _drain_model_stream(
        self,
        output: asyncio.Queue,
        stream: _ModelStream,
        out_idx: int,
    ) -> int:
        """Consume one model's queue, remapping indices and rendering tool_use as text.

        Returns the next globally-unique content_block index to use after this
        model's blocks have been emitted.
        """
        index_map: dict[int, int] = {}
        # For tool_use blocks: collect metadata and partial JSON, emit labeled text on stop.
        tool_meta: dict[int, tuple[str, str]] = {}
        tool_json: dict[int, str] = {}

        while True:
            event = await stream.queue.get()
            if event is None:
                return out_idx
            etype = getattr(event, "type", None)

            if etype == "content_block_start":
                local_idx = event.index
                cb = event.content_block
                remapped = out_idx
                index_map[local_idx] = remapped

                if cb.type == "tool_use":
                    tool_meta[local_idx] = (cb.name or "", cb.id or "")
                    tool_json[local_idx] = ""
                    await output.put(
                        RawContentBlockStartEvent.model_construct(
                            type="content_block_start",
                            index=remapped,
                            content_block=TextBlock(type="text", text=""),
                        ),
                    )
                elif cb.type == "text":
                    await output.put(
                        RawContentBlockStartEvent.model_construct(
                            type="content_block_start",
                            index=remapped,
                            content_block=TextBlock(type="text", text=""),
                        ),
                    )
                else:
                    # Unknown block types (thinking, etc.): start a text block; deltas will drop.
                    await output.put(
                        RawContentBlockStartEvent.model_construct(
                            type="content_block_start",
                            index=remapped,
                            content_block=TextBlock(type="text", text=""),
                        ),
                    )

            elif etype == "content_block_delta":
                local_idx = event.index
                if local_idx not in index_map:
                    continue
                remapped = index_map[local_idx]
                delta = event.delta
                dtype = getattr(delta, "type", None)

                if dtype == "text_delta":
                    await output.put(
                        RawContentBlockDeltaEvent.model_construct(
                            type="content_block_delta",
                            index=remapped,
                            delta=TextDelta(type="text_delta", text=getattr(delta, "text", "") or ""),
                        ),
                    )
                elif dtype == "input_json_delta" and local_idx in tool_meta:
                    tool_json[local_idx] += getattr(delta, "partial_json", "") or ""
                # Other delta kinds (thinking, signature) are dropped in MVP.

            elif etype == "content_block_stop":
                local_idx = event.index
                if local_idx not in index_map:
                    continue
                remapped = index_map.pop(local_idx)

                if local_idx in tool_meta:
                    name, tool_id = tool_meta.pop(local_idx)
                    raw = tool_json.pop(local_idx, "")
                    rendered = _render_tool_use_as_text(name, tool_id, raw)
                    await output.put(
                        RawContentBlockDeltaEvent.model_construct(
                            type="content_block_delta",
                            index=remapped,
                            delta=TextDelta(type="text_delta", text=rendered),
                        ),
                    )

                await output.put(RawContentBlockStopEvent(type="content_block_stop", index=remapped))
                out_idx = remapped + 1

            # message_start/message_delta/message_stop are captured in feed layer; ignore here.

    # -------------------------------------------------------------------------
    # Non-streaming fan-out
    # -------------------------------------------------------------------------

    async def _launch_complete(
        self,
        state: _MultiBackendState,
        request: AnthropicRequest,
        credential: Credential,
        extra_headers: dict[str, str] | None,
    ) -> None:
        """Start parallel ``client.complete`` tasks for models[1:]."""
        for idx in range(1, len(self._models)):
            extra_request = _request_for_model(request, self._models[idx])
            delay = idx * self.config.stagger_seconds
            task = asyncio.create_task(
                self._run_extra_complete(extra_request, credential, extra_headers, delay),
                name=f"multi_backend_extra_complete[{idx}]",
            )
            state.complete_tasks.append(task)

    async def _run_extra_complete(
        self,
        request: AnthropicRequest,
        credential: Credential,
        extra_headers: dict[str, str] | None,
        delay_seconds: float,
    ) -> AnthropicResponse:
        """Run a single non-streaming extra call."""
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        client = self._client_for_credential(credential)
        try:
            return await client.complete(request, extra_headers=extra_headers)
        finally:
            await client.close()

    def _build_aggregated_response(
        self,
        results: list[AnthropicResponse | BaseException],
    ) -> AnthropicResponse:
        """Combine N model responses into a single labeled AnthropicResponse."""
        content_blocks: list = []
        total_in = 0
        total_out = 0

        for model_name, result in zip(self._models, results):
            content_blocks.append({"type": "text", "text": f"# {model_name}\n\n"})
            if isinstance(result, BaseException):
                content_blocks.append(
                    {
                        "type": "text",
                        "text": f"**Error:** {self._format_error(result)}\n\n",
                    },
                )
                continue
            # Flatten content blocks into text; tool_use rendered as labeled text.
            text_segments: list[str] = []
            for block in result.get("content", []):
                btype = block.get("type")
                if btype == "text":
                    text_segments.append(block.get("text", ""))
                elif btype == "tool_use":
                    text_segments.append(
                        _render_tool_use_as_text(
                            block.get("name", ""),
                            block.get("id", ""),
                            json.dumps(block.get("input", {})),
                        ),
                    )
                # Other block types ignored in MVP.
            if text_segments:
                content_blocks.append({"type": "text", "text": "".join(text_segments) + "\n\n"})
            usage = result.get("usage", {})
            total_in += usage.get("input_tokens", 0) or 0
            total_out += usage.get("output_tokens", 0) or 0

        return AnthropicResponse(
            id=f"msg_multi_{uuid.uuid4().hex[:24]}",
            type="message",
            role="assistant",
            content=content_blocks,
            model=self._aggregated_model_label(),
            stop_reason="end_turn",
            stop_sequence=None,
            usage=build_usage(total_in, total_out),
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _new_state(self) -> _MultiBackendState:
        streams = [_ModelStream(idx=i, name=name, queue=asyncio.Queue()) for i, name in enumerate(self._models)]
        return _MultiBackendState(
            streams=streams,
            arrivals=asyncio.Queue(),
            output_queue=asyncio.Queue(),
        )

    def _client_for_credential(self, credential: Credential) -> AnthropicClient:
        """Build an AnthropicClient that forwards the caller's credential.

        Separate method so tests can override the client construction.
        """
        if credential.credential_type == CredentialType.API_KEY:
            return AnthropicClient(api_key=credential.value)
        return AnthropicClient(auth_token=credential.value)

    @staticmethod
    def _extra_headers_from_context(context: "PolicyContext") -> dict[str, str] | None:
        """Forward the caller's ``anthropic-beta`` header to fan-out calls.

        Without this, OAuth requests (Claude Code) fail with "OAuth authentication
        is currently not supported" because the OAuth beta flag is carried in the
        anthropic-beta header.

        The ``context-1m-*`` beta (1M-token context window) is a per-model
        paid add-on. Claude Code often sets it globally even if the caller's
        subscription only covers it for some models — stripping it here lets
        fan-out hit all configured models regardless of per-model entitlement.
        """
        raw = context.raw_http_request
        if raw is None:
            return None
        beta = raw.headers.get("anthropic-beta")
        if not beta:
            return None
        filtered = [
            token.strip() for token in beta.split(",") if token.strip() and not token.strip().startswith("context-1m-")
        ]
        if not filtered:
            return None
        return {"anthropic-beta": ",".join(filtered)}

    def _aggregated_model_label(self) -> str:
        return "multi[" + ",".join(self._models) + "]"

    @staticmethod
    def _drain_output_nowait(state: _MultiBackendState) -> list[MessageStreamEvent]:
        out: list[MessageStreamEvent] = []
        while True:
            try:
                item = state.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                return out
            if item is None:
                continue  # coordinator's terminal sentinel; don't forward
            out.append(cast(MessageStreamEvent, item))

    @staticmethod
    def _format_error(e: BaseException) -> str:
        return f"{type(e).__name__}: {e}"

    @staticmethod
    async def _emit_header_block(output: asyncio.Queue, idx: int, model_name: str) -> int:
        """Emit a labeled header block; returns next index."""
        await output.put(
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=idx,
                content_block=TextBlock(type="text", text=""),
            ),
        )
        await output.put(
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=idx,
                delta=TextDelta(type="text_delta", text=f"# {model_name}\n\n"),
            ),
        )
        await output.put(RawContentBlockStopEvent(type="content_block_stop", index=idx))
        return idx + 1

    @staticmethod
    async def _emit_error_block(output: asyncio.Queue, idx: int, error_text: str) -> int:
        await output.put(
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=idx,
                content_block=TextBlock(type="text", text=""),
            ),
        )
        await output.put(
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=idx,
                delta=TextDelta(type="text_delta", text=f"**Error:** {error_text}\n\n"),
            ),
        )
        await output.put(RawContentBlockStopEvent(type="content_block_stop", index=idx))
        return idx + 1


# Per-model-family field compatibility rules.
#
# The gateway forwards the client's full request (including advanced features
# like ``thinking``, ``context_management``, ``output_config``, ``effort``) to
# each configured model. Not every model supports every field — haiku-4 in
# particular rejects adaptive thinking, clear_thinking context edits, and the
# effort parameter. Each entry is (model-id prefix, fields to drop); the first
# matching prefix wins. Fields are ordered so that dependencies stay
# self-consistent (e.g. stripping ``thinking`` also strips the
# ``clear_thinking_*`` strategies that reference it).
_MODEL_FIELD_DROPS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "claude-haiku-",
        frozenset({"thinking", "context_management", "effort", "output_config"}),
    ),
    # Opus and Sonnet 4.x accept thinking + clear_thinking context edits
    # together; leaving them intact requires no drops here. Add rules if/when
    # a new model reports incompatibility.
)


def _fields_to_drop_for_model(model: str) -> frozenset[str]:
    for prefix, drops in _MODEL_FIELD_DROPS:
        if model.startswith(prefix):
            return drops
    return frozenset()


def _request_for_model(request: AnthropicRequest, target_model: str) -> AnthropicRequest:
    """Return a deep copy of ``request`` targeted at ``target_model``.

    Drops fields the target model doesn't support (see ``_MODEL_FIELD_DROPS``)
    so the backend call meets the model's specific requirements instead of a
    lowest-common-denominator shape.
    """
    drops = _fields_to_drop_for_model(target_model)
    kept: dict = {k: copy.deepcopy(v) for k, v in request.items() if k not in drops}
    kept["model"] = target_model
    return cast(AnthropicRequest, kept)


def _render_tool_use_as_text(name: str, tool_id: str, input_json: str) -> str:
    """Render a tool_use block as a labeled-text placeholder.

    The policy aggregates outputs from multiple models. Forwarding tool_use
    blocks verbatim would produce ambiguous semantics for the client (multiple
    models each requesting overlapping tool calls), so MVP renders them as
    inert labeled text.
    """
    stripped = input_json.strip() if input_json else ""
    return f"[tool_use: {name}({stripped})]\n"


__all__ = ["MultiBackendPolicy", "MultiBackendConfig"]
