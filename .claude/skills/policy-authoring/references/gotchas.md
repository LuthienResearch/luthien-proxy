# Policy Gotchas Reference

Non-obvious behaviors, edge cases, and common mistakes when writing Luthien policies.

## Mutable State on Policy Instances

**Problem:** Policies are singletons shared across all concurrent requests. Mutable containers on `self` would be shared state.

**Guard:** `freeze_configured_state()` runs at load time and rejects `list`, `dict`, `set`, `bytearray` on the instance.

```python
# WRONG — freeze_configured_state() will raise TypeError
class BadPolicy(BasePolicy, AnthropicHookPolicy):
    def __init__(self):
        self.cache = {}            # MutableMapping
        self.patterns = ["a", "b"] # MutableSequence

# RIGHT — use immutable types for config state
class GoodPolicy(BasePolicy, AnthropicHookPolicy):
    def __init__(self):
        self._patterns = ("a", "b")          # tuple
        self._names = frozenset({"x", "y"})  # frozenset

# RIGHT — use PolicyContext for request-scoped mutable state
@dataclass
class _RequestState:
    buffer: dict[int, str] = field(default_factory=dict)

class GoodPolicy2(BasePolicy, AnthropicHookPolicy):
    async def on_anthropic_stream_event(self, event, context):
        state = context.get_request_state(self, _RequestState, _RequestState)
        state.buffer[0] = "safe"  # isolated per request
```

Note: `freeze_configured_state()` only checks at load time. It does not prevent runtime attribute assignment. But any runtime mutation on the singleton is a concurrency bug.

**Exception:** Pydantic `BaseModel` instances assigned to `self.config` are allowed because they're not `MutableMapping`/`MutableSequence`/`MutableSet` — but treat them as immutable anyway.

---

## Streaming Event Ordering

### Content Blocks Must Precede message_delta

**Problem:** If a policy emits `content_block_*` events after `RawMessageDeltaEvent`, the Anthropic client session is corrupted.

**Fix:** Flush all held-back content events before message_delta arrives.

```python
# In on_anthropic_stream_event:
if isinstance(event, RawMessageDeltaEvent):
    flush_events = self._flush_buffer(state)  # emit pending content
    flush_events.append(event)
    return flush_events
```

This is why `TextModifierPolicy._flush_before_message_delta()` and `StringReplacementPolicy`'s buffer flush both trigger on `RawMessageDeltaEvent`.

### Thinking Blocks Must Come First

Anthropic API requires `thinking`/`redacted_thinking` blocks BEFORE `text` blocks in response content. Reordering blocks to put text first causes: `Expected 'thinking' or 'redacted_thinking', but found 'text'`.

### Single finish_reason in Final Chunk

**Problem:** Setting `finish_reason` on each tool call chunk causes clients to interpret them as separate responses.

**Fix:** Emit `finish_reason` only once, in the final chunk after all tool calls.

---

## Content + finish_reason in Same Chunk

**Problem:** When a streaming chunk contains both content text AND a `finish_reason`, some clients ignore the `finish_reason` — the content takes priority and the stop signal is lost.

**Fix:** Emit content as one chunk, then emit a separate chunk with only the `finish_reason`.

---

## Preflight Requests (Claude Code Probes)

**Problem:** Claude Code sends preflight/probe requests with `max_tokens=1` that share the same `session_id` as the real request. If a policy uses session-level state to track "first turn", the preflight consumes it.

**Fix:** Use `is_first_turn(request)` which checks message count (re-evaluated per request), not session-level counters. Each request gets its own `PolicyContext`, so any state set during preflight is scoped to that request's context.

---

## SimplePolicy: Thinking Deltas Pass Through

`SimplePolicy` only buffers `TextDelta` and `InputJSONDelta`. Other delta types (thinking blocks, etc.) pass through unchanged. This is by design — `simple_on_response_content()` only sees complete text, not thinking content.

---

## Tool Call Replacement: Stop Reason

When blocking a tool_use and replacing it with text, also update `stop_reason`:

```python
# Non-streaming: check if all tool_use blocks were blocked
has_tool_use = any(b.get("type") == "tool_use" for b in new_content)
if not has_tool_use and response.get("stop_reason") == "tool_use":
    response["stop_reason"] = "end_turn"
```

Without this, the client thinks it should submit tool results but has no tool calls to respond to.

---

## model_construct vs Regular Construction

For performance in hot paths (streaming), use `model_construct` to skip Pydantic validation:

```python
# Fast — no validation (use in streaming hot path)
text_delta = TextDelta.model_construct(type="text_delta", text=transformed)
delta_event = RawContentBlockDeltaEvent.model_construct(
    type="content_block_delta", index=index, delta=text_delta,
)

# Normal — with validation (use when correctness matters more than speed)
delta_event = RawContentBlockDeltaEvent(
    type="content_block_delta", index=index, delta=text_delta,
)
```

`SimplePolicy` and `StringReplacementPolicy` use `model_construct` in their streaming paths.
`TextModifierPolicy` uses regular constructors for suffix injection (less frequent, correctness matters).

---

## model_copy for Event Modification

To modify an existing event without mutation, use `model_copy`:

```python
# Modify a text delta in an existing event
new_delta = event.delta.model_copy(update={"text": transformed_text})
return [event.model_copy(update={"delta": new_delta})]
```

This preserves all other fields and creates a new object (no shared state).

---

## Buffer Cleanup

Always clean up request-scoped state when streaming completes. There are two options:

### Option A: `on_anthropic_stream_complete` (standard protocol hook)

This is the fourth lifecycle hook defined on `AnthropicExecutionInterface`/`AnthropicHookPolicy`. The executor always calls it after the upstream stream ends. Use this as the primary cleanup path:

```python
async def on_anthropic_stream_complete(self, context):
    state = context.pop_request_state(self, _MyState)
    if state and state.buffer:
        return self._flush(state)  # emit remaining buffered content
    return []
```

### Option B: `on_anthropic_streaming_policy_complete` (convention method, MultiSerialPolicy only)

Several built-in policies (`SimplePolicy`, `ToolCallJudgePolicy`, `DogfoodSafetyPolicy`) define an `on_anthropic_streaming_policy_complete` cleanup method. **This is not part of any protocol** — it's a convention method discovered via `getattr` in `MultiSerialPolicy.on_anthropic_streaming_policy_complete()` (see `src/luthien_proxy/policies/multi_serial_policy.py:170-175`).

It is **only called when policies are composed via `MultiSerialPolicy`**. If the policy runs standalone (the default), this method will never fire. Do not rely on it for correctness-critical cleanup — use `on_anthropic_stream_complete` instead. Only define it if the policy will specifically be used as a sub-policy in a chain and needs a cleanup hook that doesn't return events.

---

## Config Instantiation Patterns

The `_instantiate_policy()` function in `config.py` tries two patterns:

1. **Spread pattern:** `policy_class(**config_dict)` — when config keys match constructor param names
2. **Model pattern:** `policy_class(param_name=config_dict)` — when config keys match Pydantic model fields

If a policy has `def __init__(self, config: MyConfig | None = None)`, the YAML `config:` section is passed as the `config` parameter. If it has `def __init__(self, threshold: float = 0.5, model: str = "...")`, the YAML keys are spread as keyword arguments.

**Recommendation:** Use the single-parameter Pydantic model pattern (`config: MyConfig | None = None`) for consistency and auto-serialization via `get_config()`.

---

## Observability Best Practices

Use `context.record_event()` for policy-specific telemetry:

```python
context.record_event(
    "policy.my_policy.blocked",
    {
        "summary": f"Blocked tool call: {name}",  # human-readable
        "tool_name": name,
        "reason": "matched pattern",
    },
)
```

Conventions:
- Event type: `policy.<policy_name>.<action>` (e.g., `policy.judge.evaluation_complete`)
- Include a `summary` field for UI display
- Include a `severity` field for warnings/errors: `"warning"`, `"error"`
- Truncate large values (tool arguments → first 500 chars)

Use `context.span(name, attrs)` for timed operations:

```python
with context.span("judge_evaluation", {"tool_name": name}):
    result = await self._call_judge(...)
```

---

## cast() for MessageStreamEvent

When constructing `RawContentBlock*` events manually, they need to be cast to `MessageStreamEvent` for the return type:

```python
from typing import cast
from anthropic.lib.streaming import MessageStreamEvent

return [
    cast(MessageStreamEvent, start_event),
    cast(MessageStreamEvent, delta_event),
    cast(MessageStreamEvent, stop_event),
]
```

This is a type-checker appeasement — the runtime types are correct.
