---
name: Policy Authoring
description: This skill should be used when the user needs to create, modify, or understand Luthien gateway policies — intercepting and transforming Anthropic API traffic. Covers "create a policy", "write a policy", "implement a policy", "modify a policy", "policy example", "how do policies work", "block a tool call", "filter streaming events", "transform responses", "intercept requests", or mentions PolicyContext, SimplePolicy, TextModifierPolicy, AnthropicHookPolicy, or policy hooks.
version: 0.1.0
---

# Luthien Policy Authoring Guide

Luthien policies intercept and transform Anthropic API traffic flowing through the gateway. Policies are singletons created once at startup, shared across all concurrent requests. They implement four lifecycle hooks called by the executor around backend I/O.

## Policy Class Hierarchy — Pick the Right Base

| Base class | Inherit when... | Override |
|---|---|---|
| `BasePolicy` + `AnthropicHookPolicy` | Full control over every hook | Any of the 4 hooks |
| `TextModifierPolicy` | Transforming response text (streaming-aware) | `modify_text()`, optionally `extra_text()` |
| `SimplePolicy` | Buffering is acceptable; transform complete content | `simple_on_response_content()`, `simple_on_request()`, `simple_on_anthropic_tool_call()` |
| `NoOpPolicy` | Pass-through base for config-only demos | Nothing (inherits passthrough) |

**Decision flowchart:**
1. Only modifying text content? -> `TextModifierPolicy` (streaming-friendly, 1 method)
2. Need complete content blocks (e.g., tool calls)? -> `SimplePolicy` (buffers, 3 methods)
3. Need per-event streaming control? -> `BasePolicy` + `AnthropicHookPolicy` (4 hooks)

## The Four Lifecycle Hooks

```
on_anthropic_request(request, context) -> request       # Transform before backend call
      |
[Backend call happens]
      |
on_anthropic_response(response, context) -> response    # Non-streaming only
      |
# Streaming path:
on_anthropic_stream_event(event, context) -> list[event] # Per-event (filter=[], duplicate=multi)
on_anthropic_stream_complete(context) -> list[emission]   # After stream ends, inject extras
```

`AnthropicHookPolicy` provides passthrough defaults for all four — override only what's needed.

## Request-Scoped State via PolicyContext

Policies are singletons. **Never store request data on `self`.**

```python
@dataclass
class _MyState:
    buffer: str = ""
    count: int = 0

class MyPolicy(BasePolicy, AnthropicHookPolicy):
    async def on_anthropic_stream_event(self, event, context):
        state = context.get_request_state(self, _MyState, _MyState)
        state.buffer += "..."   # safe — isolated per request per policy
        return [event]
```

Key `PolicyContext` facilities:
- `get_request_state(owner, expected_type, factory)` — typed, collision-free per-policy state
- `pop_request_state(owner, type)` — remove and return (cleanup)
- `record_event(event_type, data)` — fire-and-forget observability
- `span(name, attrs)` — OpenTelemetry context manager
- `session_id` — conversation session identifier
- `credential_manager` — resolve auth credentials
- `for_testing(...)` — create test-friendly contexts

## Configuration with Pydantic

Define a Pydantic `BaseModel` for config. Use `_init_config()` to handle all input forms (None/dict/model):

```python
class MyConfig(BaseModel):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    api_key: str | None = Field(default=None, json_schema_extra={"format": "password"})

class MyPolicy(BasePolicy, AnthropicHookPolicy):
    def __init__(self, config: MyConfig | None = None):
        self.config = self._init_config(config, MyConfig)
        # Derived immutable state is fine on self:
        self._threshold = self.config.threshold  # scalar — OK
```

YAML config:
```yaml
policy:
  class: "luthien_proxy.policies.my_policy:MyPolicy"
  config:
    threshold: 0.8
```

## Critical Rules

1. **No mutable containers on `self`** — `freeze_configured_state()` rejects `list`, `dict`, `set` on the policy instance. Use `tuple`/`frozenset` for config, `PolicyContext` for request state.
2. **Content blocks before message_delta** — all `content_block_*` events must precede `RawMessageDeltaEvent` in streaming. Violating this corrupts sessions.
3. **Thinking blocks first** — `thinking`/`redacted_thinking` blocks must come before `text` blocks.
4. **Single finish_reason** — emit `finish_reason` once in the final chunk, not per tool call.
5. **Preflight detection** — Claude Code sends probe requests (`max_tokens=1`) sharing `session_id`. Use `is_first_turn()` (re-evaluated per request), not session-level counters.

## Streaming Event Types

Events from `anthropic.types`:
- `RawContentBlockStartEvent` — block begins (has `.content_block` with type info)
- `RawContentBlockDeltaEvent` — content chunk (`.delta`: `TextDelta`, `InputJSONDelta`, etc.)
- `RawContentBlockStopEvent` — block complete
- `RawMessageDeltaEvent` — message-level metadata (stop_reason, usage)
- `RawMessageStopEvent` — message complete

Return values from `on_anthropic_stream_event`:
- `[event]` — pass through
- `[]` — filter/suppress (buffer it)
- `[event1, event2]` — expand/inject

## Testing Policies

Mirror source path: `src/.../my_policy.py` -> `tests/.../unit_tests/policies/test_my_policy.py`

```python
from luthien_proxy.policy_core.policy_context import PolicyContext

@pytest.fixture
def policy():
    return MyPolicy(config=MyConfig(threshold=0.5))

class TestMyPolicyProtocol:
    def test_implements_interface(self, policy):
        assert isinstance(policy, AnthropicExecutionInterface)
        assert isinstance(policy, BasePolicy)

class TestMyPolicyResponse:
    @pytest.mark.asyncio
    async def test_transforms_content(self):
        policy = MyPolicy()
        ctx = PolicyContext.for_testing(transaction_id="test")
        response: AnthropicResponse = {
            "id": "msg_test", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": "test", "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        result = await policy.on_anthropic_response(response, ctx)
        assert result["content"][0]["text"] == "EXPECTED"
```

Run: `uv run pytest tests/luthien_proxy/unit_tests/policies/test_my_policy.py`

## File Placement

- Policy module: `src/luthien_proxy/policies/<name>_policy.py`
- Tests: `tests/luthien_proxy/unit_tests/policies/test_<name>_policy.py`
- Config: `config/policy_config.yaml` (uncomment/add entry)
- Export: add to `src/luthien_proxy/policies/__init__.py` if needed

## Additional Resources

For detailed patterns, streaming internals, and gotchas, consult:
- **`references/patterns.md`** — complete examples of each policy tier (passthrough, text modifier, buffered, streaming, judge-based)
- **`references/gotchas.md`** — streaming protocol violations, mutable state bugs, event ordering, and preflight edge cases
- **`references/uppercase_policy.py`** — simplest possible TextModifierPolicy (working example)
- **`references/first_turn_banner_policy.py`** — SimplePolicy with request-scoped state and first-turn detection (working example)
