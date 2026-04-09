"""luthien agent-tutorial -- print everything an LLM agent needs to work with the gateway."""

from __future__ import annotations

from pathlib import Path

import click

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _resolve_policies_dir() -> str:
    """Resolve the policies directory from the gateway's configured repo path.

    Checks two layouts:
    - Dev checkout: <repo_path>/src/luthien_proxy/policies/
    - Installed (venv): <repo_path>/../venv/lib/python*/site-packages/luthien_proxy/policies/
    """
    config = load_config(DEFAULT_CONFIG_PATH)
    if config.repo_path:
        repo = Path(config.repo_path)

        # Dev checkout layout
        candidate = repo / "src" / "luthien_proxy" / "policies"
        if candidate.is_dir():
            return str(candidate)

        # Installed venv layout (~/.luthien/venv/lib/python*/site-packages/...)
        venv_dir = repo.parent / "venv"
        if venv_dir.is_dir():
            matches = list(venv_dir.glob("lib/python*/site-packages/luthien_proxy/policies"))
            if matches:
                return str(matches[0])

    return "<repo_path>/src/luthien_proxy/policies"


TUTORIAL_TEMPLATE = """\
# Luthien Proxy — Agent Tutorial

You are interacting with a Luthien proxy gateway running on localhost.
This tutorial tells you everything you need to manage and create policies.

Note: `! cmd` in Claude Code runs `cmd` in the shell.

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
luthien policy set MyPolicy --config '{{"threshold": 0.8}}'

# Show the currently active policy
luthien policy current
```

## 2. Writing a New Policy

Policies live in:
```
{policies_dir}
```

A policy is a Python class that inherits from `BasePolicy` and
`AnthropicHookPolicy`, then overrides async hooks to inspect or modify
requests and responses.

### Minimal example (pass-through)

```python
from luthien_proxy.policy_core import BasePolicy, AnthropicHookPolicy

class MyPolicy(BasePolicy, AnthropicHookPolicy):
    pass
```

This does nothing — all hooks default to pass-through.

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
from anthropic.lib.streaming import MessageStreamEvent
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policy_core import AnthropicPolicyEmission
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
luthien policy set MyPolicy --config '{{"threshold": 0.8, "keywords": ["foo"]}}'
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
- **Hot-swap works for existing policies.** Switching between already-loaded
  policies is instant — no restart needed. But creating a new policy file or
  editing an existing one requires a gateway restart to clear Python's module
  cache and the policy discovery cache.

## 4. Workflow: Create and Activate a Policy

1. Create a new file in the policies directory shown above
2. Define your policy class (see examples above)
3. Restart the gateway to discover the new policy: `luthien restart`
4. Activate it: `luthien policy set MyPolicy`
5. Test by sending a message through the proxy
6. To iterate: edit the file, then `luthien restart` and re-activate

**Note:** if you are an agent running through this proxy, `luthien restart`
will briefly interrupt your connection. Your client will retry automatically.
"""


@click.command("agent-tutorial")
def agent_tutorial():
    """Print a tutorial for LLM agents on working with the gateway.

    Outputs everything an AI coding agent needs to manage policies,
    write new ones, and interact with the admin API.
    """
    policies_dir = _resolve_policies_dir()
    click.echo(TUTORIAL_TEMPLATE.format(policies_dir=policies_dir))
