"""luthien agent-tutorial -- print everything an LLM agent needs to work with the gateway."""

import click


TUTORIAL = """\
# Luthien Proxy — Agent Tutorial

You are interacting with a Luthien proxy gateway running on localhost.
This tutorial tells you everything you need to manage and create policies.

## Assumptions

- The gateway is running locally (dockerless mode, localhost auth bypass enabled).
- Admin API calls from localhost need no authentication.
- The gateway URL is http://localhost:8000 (override with `luthien config`).

---

## 1. Managing Policies via the CLI

```bash
# List all available policies (> marks active)
luthien policy list

# Show details for a specific policy
luthien policy show NoOpPolicy

# Activate a policy by name
luthien policy set NoOpPolicy

# Activate with config
luthien policy set MyPolicy --config '{"threshold": 0.8}'

# Show the currently active policy
luthien policy current
```

## 2. Writing a New Policy

Policies live in `src/luthien_proxy/policies/`. A policy is a Python class that
inherits from `BasePolicy` and `AnthropicHookPolicy`, then overrides async hooks
to inspect or modify requests and responses.

### Minimal example (pass-through)

```python
from luthien_proxy.policy_core import BasePolicy, AnthropicHookPolicy

class MyPolicy(BasePolicy, AnthropicHookPolicy):
    pass
```

This does nothing — all hooks default to pass-through. Activate it with:
```bash
luthien policy set MyPolicy
```

### Lifecycle hooks (all optional, all async)

```python
async def on_anthropic_request(
    self, request: AnthropicRequest, context: PolicyContext
) -> AnthropicRequest:
    # Inspect or modify the request before it goes to the LLM.
    # `request` is a dict matching the Anthropic Messages API schema.
    return request

async def on_anthropic_response(
    self, response: AnthropicResponse, context: PolicyContext
) -> AnthropicResponse:
    # Inspect or modify non-streaming responses.
    return response

async def on_anthropic_stream_event(
    self, event: MessageStreamEvent, context: PolicyContext
) -> list[MessageStreamEvent]:
    # Inspect or modify each streaming event. Must return a list.
    return [event]

async def on_anthropic_stream_complete(
    self, context: PolicyContext
) -> list[AnthropicPolicyEmission]:
    # Emit extra events after the stream ends. Usually return [].
    return []
```

Import types from:
```python
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policy_core.policy_context import PolicyContext
```

### Text-only shortcut: TextModifierPolicy

For policies that only transform response text, inherit from `TextModifierPolicy`
instead. It handles all streaming/non-streaming plumbing for you:

```python
from luthien_proxy.policy_core import TextModifierPolicy

class ShoutPolicy(TextModifierPolicy):
    def modify_text(self, text: str) -> str:
        return text.upper()
```

`TextModifierPolicy` also supports `extra_text()` to append text to the response.

### Adding configuration

Define a Pydantic model and use `_init_config()`:

```python
from pydantic import BaseModel, Field
from luthien_proxy.policy_core import BasePolicy, AnthropicHookPolicy

class MyConfig(BaseModel):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    keywords: list[str] = Field(default_factory=list)

class MyPolicy(BasePolicy, AnthropicHookPolicy):
    def __init__(self, config: MyConfig | dict | None = None):
        self.config = self._init_config(config, MyConfig)
        # IMPORTANT: convert mutable collections to immutable
        self._keywords = tuple(self.config.keywords)
```

Activate with config:
```bash
luthien policy set MyPolicy --config '{"threshold": 0.8, "keywords": ["foo"]}'
```

## 3. Critical Constraints

- **Policies are singletons.** One instance is created at startup and shared
  across all concurrent requests. Never store request-scoped state on `self`.
- **Use PolicyContext for per-request state:**
  ```python
  from dataclasses import dataclass

  @dataclass
  class MyState:
      seen_tool_use: bool = False

  state = context.get_request_state(self, MyState, MyState)
  state.seen_tool_use = True  # safe — scoped to this request
  ```
- **Mutable instance attributes are rejected.** `freeze_configured_state()` runs
  at load time and raises if your policy has `dict`, `list`, `set`, or `bytearray`
  attributes. Use `tuple` or `frozenset` instead.
- **Hot-swap is instant.** The API activates the new policy for all subsequent
  requests — no gateway restart needed.

## 4. Workflow: Create and Activate a Policy

1. Create a new file in `src/luthien_proxy/policies/my_policy.py`
2. Define your policy class (see examples above)
3. Activate it: `luthien policy set MyPolicy`
4. Test by sending a message through the proxy
5. Iterate — edit the file and re-activate to pick up changes
"""


@click.command("agent-tutorial")
def agent_tutorial():
    """Print a tutorial for LLM agents on working with the gateway.

    Outputs everything an AI coding agent needs to manage policies,
    write new ones, and interact with the admin API.
    """
    click.echo(TUTORIAL)
