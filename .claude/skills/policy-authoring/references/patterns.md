# Policy Patterns Reference

Detailed examples of each policy authoring tier, from simplest to most complex.

## Pattern 1: Passthrough (NoOpPolicy)

The absolute minimum — inherit `BasePolicy` + `AnthropicHookPolicy`, all hooks pass through by default.

```python
from luthien_proxy.policy_core import AnthropicHookPolicy, BasePolicy

class NoOpPolicy(BasePolicy, AnthropicHookPolicy):
    @property
    def short_policy_name(self) -> str:
        return "NoOp"

    def active_policy_names(self) -> list[str]:
        return []  # doesn't modify anything
```

Use case: placeholder, config-only demo base.

---

## Pattern 2: TextModifierPolicy — Modify Response Text

Override `modify_text()` for in-stream text transformation. Handles both streaming and non-streaming automatically.

```python
from luthien_proxy.policy_core import TextModifierPolicy

class AllCapsPolicy(TextModifierPolicy):
    def modify_text(self, text: str) -> str:
        return text.upper()
```

### With Extra Text (Suffix Injection)

Override `extra_text()` to append content to the last text block. The base class handles the streaming protocol complexity — flushing before `message_delta`, handling tool_use blocks that follow text, etc.

```python
class OnboardingPolicy(TextModifierPolicy):
    def __init__(self, gateway_url: str = "http://localhost:8000"):
        self._gateway_url = gateway_url

    def extra_text(self) -> str | None:
        return f"\n\n---\nGateway: {self._gateway_url}"
```

**How TextModifierPolicy handles streaming internally:**
- Text deltas are modified in-flight (`model_copy(update=...)`)
- `extra_text()` suffix is injected before `RawMessageDeltaEvent` (content blocks must precede it)
- If a `ToolUseBlock` starts after text, the held-back text stop event is flushed with the suffix first
- Safety net in `on_anthropic_stream_complete` handles abrupt stream endings

---

## Pattern 3: SimplePolicy — Buffered Content Transformation

Trades streaming responsiveness for simpler authoring. Buffers all content, applies transformation on `content_block_stop`, emits as a single delta.

### Response Content Transformation

```python
from luthien_proxy.policies.simple_policy import SimplePolicy

class CensorPolicy(SimplePolicy):
    async def simple_on_response_content(self, content: str, context) -> str:
        return content.replace("secret", "[REDACTED]")
```

### Request Transformation

```python
class SystemPromptInjector(SimplePolicy):
    async def simple_on_request(self, request_str: str, context) -> str:
        return f"[IMPORTANT: Always be helpful]\n{request_str}"
```

### Tool Call Transformation

```python
from luthien_proxy.llm.types.anthropic import AnthropicToolUseBlock

class ToolCallLogger(SimplePolicy):
    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context
    ) -> AnthropicToolUseBlock:
        context.record_event("tool_called", {"name": tool_call["name"]})
        return tool_call  # pass through unchanged
```

### How SimplePolicy Buffering Works

1. `content_block_start` → initialize buffer for this block index
2. `content_block_delta` (TextDelta) → accumulate in buffer, return `[]` (suppress)
3. `content_block_delta` (InputJSONDelta) → accumulate in tool buffer, return `[]`
4. `content_block_stop` → call `simple_on_response_content()` or `simple_on_anthropic_tool_call()`, emit transformed content as single delta + stop event
5. Other events (thinking deltas, message events) → pass through unchanged

**Request-scoped state** is managed via `_SimplePolicyAnthropicState` using `context.get_request_state()`.

---

## Pattern 4: Full Hook Policy — Streaming Control

For policies that need per-event streaming decisions (filter events, inject events, buffer across chunks).

### Tool Call Buffering and Evaluation (Judge Pattern)

The `ToolCallJudgePolicy` pattern: buffer tool_use events, evaluate on completion, either replay or replace.

```python
@dataclass
class _BufferedToolUse:
    id: str
    name: str
    input_json: str = ""

@dataclass
class _JudgeState:
    buffered: dict[int, _BufferedToolUse] = field(default_factory=dict)

class MyJudgePolicy(BasePolicy, AnthropicHookPolicy):
    def _state(self, ctx):
        return ctx.get_request_state(self, _JudgeState, _JudgeState)

    async def on_anthropic_stream_event(self, event, context):
        state = self._state(context)

        # Buffer tool_use starts
        if isinstance(event, RawContentBlockStartEvent):
            if isinstance(event.content_block, ToolUseBlock):
                state.buffered[event.index] = _BufferedToolUse(
                    id=event.content_block.id,
                    name=event.content_block.name,
                )
                return []  # suppress until judged
            return [event]

        # Accumulate JSON deltas
        if isinstance(event, RawContentBlockDeltaEvent):
            if event.index in state.buffered and isinstance(event.delta, InputJSONDelta):
                state.buffered[event.index].input_json += event.delta.partial_json
                return []
            return [event]

        # Judge on block completion
        if isinstance(event, RawContentBlockStopEvent):
            if event.index in state.buffered:
                buf = state.buffered.pop(event.index)
                if self._should_block(buf):
                    return self._build_blocked_events(event.index, buf, event)
                return self._rebuild_allowed_events(event.index, buf, event)
            return [event]

        return [event]
```

### Rebuilding Allowed Events

When a buffered tool call is allowed, reconstruct the full event sequence:

```python
def _rebuild_allowed_events(self, index, buf, stop_event):
    start = RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=buf.id, name=buf.name, input={}),
    )
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=buf.input_json or "{}"),
    )
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta), cast(MessageStreamEvent, stop_event)]
```

### Replacing Blocked Tool Calls with Text

```python
def _build_blocked_events(self, index, buf, stop_event):
    start = RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=TextBlock(type="text", text=""),
    )
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=f"Blocked: {buf.name}"),
    )
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta), cast(MessageStreamEvent, stop_event)]
```

---

## Pattern 5: Cross-Chunk Streaming (Sliding Buffer)

`StringReplacementPolicy` demonstrates handling replacements that span chunk boundaries using a sliding buffer.

**Core idea:** Hold back `buffer_size` characters (longest source - 1) from each chunk. On the next chunk, prepend the buffer, apply replacements to the combined string, emit the safe prefix, hold back the new tail.

```python
# Simplified version of the sliding buffer pattern
async def on_anthropic_stream_event(self, event, context):
    if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
        state = self._get_buffer_state(context)
        combined = state.buffer + event.delta.text
        replaced = self._apply_replacements(combined)

        if len(replaced) <= self._buffer_size:
            state.buffer = replaced
            return []  # not enough to emit safely

        emit_text = replaced[:-self._buffer_size]
        state.buffer = replaced[-self._buffer_size:]
        new_delta = event.delta.model_copy(update={"text": emit_text})
        return [event.model_copy(update={"delta": new_delta})]

    # Flush buffer before message_delta and content_block_stop
    if isinstance(event, (RawMessageDeltaEvent, RawContentBlockStopEvent)):
        flush_events = self._flush_buffer(state)
        flush_events.append(event)
        return flush_events
```

**Critical:** Always flush before `RawMessageDeltaEvent` and `RawContentBlockStopEvent`.

---

## Pattern 6: Pydantic Config with Immutable Derived State

```python
class MyConfig(BaseModel):
    replacements: list[list[str]] = Field(default_factory=list)
    match_capitalization: bool = False

class MyPolicy(BasePolicy, AnthropicHookPolicy):
    def __init__(self, config: MyConfig | None = None):
        self.config = self._init_config(config, MyConfig)
        # Derived state MUST be immutable (freeze_configured_state enforces this)
        self._replacements: tuple[tuple[str, str], ...] = tuple(
            (pair[0], pair[1]) for pair in self.config.replacements
        )
        self._buffer_size: int = max(
            (len(s) for s, _ in self._replacements), default=0
        ) - 1
```

---

## Pattern 7: First-Turn Detection

Detect whether this is the first message in a conversation:

```python
from luthien_proxy.policies.onboarding_policy import is_first_turn

class MyPolicy(SimplePolicy):
    async def on_anthropic_request(self, request, context):
        state = self._state(context)
        state.first_turn = is_first_turn(request)
        return await super().on_anthropic_request(request, context)

    async def simple_on_response_content(self, content, context):
        if not self._state(context).first_turn:
            return content
        return f"[Welcome banner]\n\n{content}"
```

`is_first_turn()` checks if the request has only a single user message (no prior exchanges).

---

## Pattern 8: External LLM Calls (Judge)

For policies that call a separate LLM to evaluate content:

```python
from luthien_proxy.llm.judge_client import judge_completion

class MyJudge(BasePolicy, AnthropicHookPolicy):
    def __init__(self, config=None):
        self.config = self._init_config(config, MyJudgeConfig)
        self._auth_provider = parse_auth_provider(self.config.auth_provider)

    async def _call_judge(self, name, arguments, context):
        credential = await context.credential_manager.resolve(self._auth_provider, context)
        response_text = await judge_completion(
            credential,
            model=self.config.model,
            messages=prompt,
            temperature=0.0,
            max_tokens=256,
            api_base=self.config.api_base,
        )
        return parse_result(response_text)
```

**Fail-secure:** Always wrap judge calls in try/except. Default to blocking on failure.

---

## Policy Composition

Policies can be chained via `MultiSerialPolicy`:

```python
from luthien_proxy.policies.policy_composition import compose_policy

# At runtime (e.g., via admin API):
combined = compose_policy(current_policy, additional_policy)
# Request hooks run in order: policy1 -> policy2
# Each policy's output feeds the next
```

`DogfoodSafetyPolicy` auto-composes via `DOGFOOD_MODE=true` — wraps whatever policy is configured.

---

## Admin API Hot-Swap

Policies can be changed at runtime via the admin API:

```
POST /api/admin/policy
{
    "class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
    "config": {},
    "enabled_by": "admin"
}
```

The policy manager validates the class, instantiates it, and swaps atomically.
