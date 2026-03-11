# policies — Concrete Policy Implementations

## OVERVIEW

All policy classes live here. Subclass `SimplePolicy` for content transforms; implement full interfaces for streaming control.

## STRUCTURE

```
policies/
├── simple_policy.py              # Convenience base: buffers streaming, 3 override points
├── noop_policy.py                # Pass-through (no-op)
├── all_caps_policy.py            # Demo: uppercases content (good reference for new policies)
├── string_replacement_policy.py  # Configurable find/replace (Pydantic config model)
├── debug_logging_policy.py       # Logs requests/responses
├── tool_call_judge_policy.py     # LLM-as-judge safety evaluation (most complex, 984 lines)
├── tool_call_judge_utils.py      # Judge helper functions
├── dogfood_safety_policy.py      # Self-protection (blocks kill commands during dogfooding)
├── sample_pydantic_policy.py     # Pydantic config example
├── simple_noop_policy.py         # Minimal SimplePolicy subclass
├── multi_serial_policy.py        # Chain policies sequentially (output N → input N+1)
├── multi_parallel_policy.py      # Run policies concurrently + consolidate
└── multi_policy_utils.py         # Shared multi-policy helpers
```

## HOW TO CREATE A NEW POLICY

**Simple content transform** (most common):
```python
from luthien_proxy.policies.simple_policy import SimplePolicy

class MyPolicy(SimplePolicy):
    async def simple_on_response_content(self, content: str, ctx: PolicyContext) -> str:
        return content.replace("foo", "bar")
```

This automatically works for both OpenAI and Anthropic, streaming and non-streaming.

**Full streaming control**: Subclass `BasePolicy` + implement `OpenAIPolicyInterface` + `AnthropicExecutionInterface`. See `tool_call_judge_policy.py` for the most complete example.

**Register**: Add to `policies/__init__.py` exports. Activate via `config/policy_config.yaml` or admin API.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Simplest example | `all_caps_policy.py` | 240 lines but includes full Anthropic support |
| Content replacement | `string_replacement_policy.py` | Shows Pydantic config model pattern |
| LLM-as-judge | `tool_call_judge_policy.py` | Most complex; calls secondary LLM |
| Policy composition | `multi_serial_policy.py` | Sequential chaining |
| Parallel evaluation | `multi_parallel_policy.py` | 5 consolidation strategies |
| SimplePolicy internals | `simple_policy.py` | Buffering logic, content/tool_call dispatch |

## CONVENTIONS (THIS DIRECTORY)

- **Stateless instances**: Policy objects are long-lived singletons. NEVER store request-scoped data as instance attributes. Use `PolicyContext.get_policy_state(self, MyState, MyState)` instead.
- **Public mutable containers forbidden**: `dict`/`list`/`set` as public attrs → rejected at load time by `freeze_configured_state()`.
- **Config via Pydantic**: Define a Pydantic `BaseModel` for config, set defaults, pass as `config` dict in YAML.
- **Both API paths**: Every policy must handle OpenAI (hooks) AND Anthropic (execution). `SimplePolicy` does this for you.
- **`__init__.py` re-exports**: All policy classes + `PolicyProtocol` + `PolicyContext` exported from package init.

## ANTI-PATTERNS

- **Emitting chunks in `on_streaming_policy_complete()`** — this hook is cleanup-only. Emit in `on_stream_complete()`.
- **Combining content + finish_reason** in one chunk — finish_reason gets silently ignored. Always separate chunks.
- **Skipping `on_content_delta()`** in policies that handle tool calls — content never reaches client.
- **Using `dict` instead of `Delta(content=text)`** — breaks SSE assembly.
- **Using `Choices` instead of `StreamingChoices`** for streaming — wrong type.
- **`finish_reason` on each tool call chunk** — clients interpret as separate responses. Emit once at end via `create_finish_chunk()`.
