# Design: `luthien hackathon` CLI Command

**Date:** 2026-03-20
**Goal:** One-command hackathon onboarding — participant runs `luthien hackathon`, gets a working gateway + forked source repo + knows exactly where to start hacking.

## Components

### 1. `hackathon.py` CLI command

**Location:** `src/luthien_cli/src/luthien_cli/commands/hackathon.py`
**Registration:** Added to `main.py` via `cli.add_command(hackathon)`

**Flow:**

1. **Welcome panel** — Rich panel explaining the hackathon and what Luthien does
2. **Fork + clone** — `gh repo fork LuthienResearch/luthien-proxy --clone` into `~/luthien-proxy` (configurable via `--path`). Falls back to `git clone https://github.com/LuthienResearch/luthien-proxy.git` if `gh` is not installed or user isn't authenticated.
   - If directory already exists AND is a luthien-proxy git repo: reuse it, `git pull`
   - If directory already exists but is NOT a luthien-proxy repo: error with message
3. **Install deps** — check `uv` is available (error with install link if not), then `uv sync --dev` in the cloned repo
4. **Start gateway from source** — uses lower-level primitives directly (NOT `_onboard_local`/`_onboard_docker`, since those assume release-artifact installation):
   - `find_free_port(8000)` to pick a port
   - Write `.env` with generated keys + SQLite config
   - Write `policy_config.yaml` with chosen policy
   - `stop_gateway()` if already running
   - Start gateway via `uv run python -m luthien_proxy.main` from the cloned repo (not the managed venv)
   - `wait_for_healthy()`
   - `save_config()` to `~/.luthien/config.toml`
5. **Policy picker** — numbered menu via `click.prompt` with choices:
   - [1] HackathonOnboardingPolicy (default) — welcome message with hackathon context
   - [2] BlockDangerousCommandsPolicy — practical safety demo
   - [3] NoYappingPolicy — removes filler/hedging
   - [4] AllCapsPolicy — simple visual demo
   - [5] NoOpPolicy — clean passthrough
6. **Create policy template** — write `hackathon_policy_template.py` into the cloned repo's `src/luthien_proxy/policies/` directory (skip if already exists)
7. **Print hackathon guide** — multiple Rich panels:
   - Cheatsheet (scripts, hot-reload, testing)
   - UI tour (key URLs with descriptions)
   - Key files to read/edit
   - Project ideas
   - Links (Discord, docs, hackathon page)
8. **Launch Claude Code** — press any key to launch through proxy with hackathon-specific prompt

**Re-running:** If someone runs `luthien hackathon` again, steps 2-3 are skipped (repo exists), gateway is restarted with the newly chosen policy, and the guide is reprinted.

**CLI signature:**
```python
@click.command()
@click.option("--path", default="~/luthien-proxy", help="Where to clone the repo")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompts")
def hackathon(path, yes):
```

Note: Docker mode is intentionally omitted. Hackathon participants use local/SQLite mode for simplicity.

### 2. `HackathonOnboardingPolicy`

**Location:** `src/luthien_proxy/policies/hackathon_onboarding_policy.py`

Extends `TextModifierPolicy`. On the first turn of a conversation, appends a hackathon-specific welcome to the response. After first turn, passes through unchanged.

The welcome message includes:
- What Luthien is and what the proxy is doing
- The gateway URL and admin UI URLs
- How to write a policy (3-line summary)
- Where the policy template is
- Top 3 project ideas
- Link to full hackathon page

Structurally identical to `OnboardingPolicy` but with hackathon-specific content.

### 3. Policy Template

**Created at:** `{cloned_repo}/src/luthien_proxy/policies/hackathon_policy_template.py`

A skeleton `SimplePolicy` subclass:

```python
"""My Hackathon Policy — [describe what it does here].

To activate:
    curl -X POST http://localhost:8000/api/admin/policy/set \
      -H "Authorization: Bearer $ADMIN_API_KEY" \
      -d '{"policy_class_ref": "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"}'

Or update config/policy_config.yaml:
    policy:
      class: "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"
      config: {}
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.llm.types.anthropic import AnthropicToolUseBlock

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext


class HackathonPolicy(SimplePolicy):
    """My hackathon policy.

    SimplePolicy buffers streaming content so you work with complete strings.
    Override any of these three methods:
    """

    async def simple_on_request(self, request_str: str, context: PolicyContext) -> str:
        """Transform the user's message before it reaches the LLM."""
        # Example: inject a system instruction
        # return request_str + "\n\nAlways respond in haiku form."
        return request_str

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Transform the LLM's text response before the user sees it."""
        # Example: append a watermark
        # return content + "\n\n[Processed by HackathonPolicy]"
        return content

    async def simple_on_anthropic_tool_call(
        self, tool_call: AnthropicToolUseBlock, context: PolicyContext
    ) -> AnthropicToolUseBlock:
        """Inspect or modify tool calls (file writes, shell commands, etc)."""
        # Example: log tool calls
        # import logging; logging.getLogger(__name__).info(f"Tool: {tool_call['name']}")
        return tool_call
```

### 4. Hackathon Guide Content

Printed to terminal after setup completes. Content organized into Rich panels:

**Panel 1: Cheatsheet**
```
Scripts:
  ./scripts/start_gateway.sh        Start gateway (no Docker)
  ./scripts/dev_checks.sh           Format + lint + typecheck + test
  uv run pytest tests/unit_tests/   Quick unit tests
  uv run pytest tests/unit_tests/policies/test_my.py -v   Test one policy

Hot-reload your policy (no restart needed):
  curl -X POST http://localhost:8000/api/admin/policy/set \
    -H "Authorization: Bearer $ADMIN_API_KEY" \
    -d '{"policy_class_ref": "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"}'

Or just edit config/policy_config.yaml and restart the gateway.
```

**Panel 2: UI Tour**
```
/policy-config              Visual policy picker and config editor
/activity/monitor           Live stream of requests and responses
/diffs                      Before/after policy transformation diffs
/request-logs/viewer        Full HTTP request/response log viewer
/conversation/live/{id}     Conversation timeline with tool calls
/health                     Gateway health check
```

**Panel 3: Key Files**
```
Start here:
  src/luthien_proxy/policies/hackathon_policy_template.py   YOUR policy
  src/luthien_proxy/policies/all_caps_policy.py             Simplest example (27 lines)
  src/luthien_proxy/policy_core/text_modifier_policy.py     Easiest base class
  config/policy_config.yaml                                  Active policy config

Go deeper:
  src/luthien_proxy/policies/simple_policy.py               Medium complexity base
  src/luthien_proxy/policies/tool_call_judge_policy.py      Advanced: LLM judge
  ARCHITECTURE.md                                            Full system design
  docs/policies.md                                           Policy reference
```

**Panel 4: Project Ideas**
```
1. Resampling Policy — if a judge rejects a response, resample instead of blocking
2. Trusted Model Reroute — route flagged tool calls to a trusted model
3. Proxy Commands — /luthien: prefixes in messages trigger proxy-side scripts
4. Live Policy Editor — ^^^describe changes^^^ inline while coding
5. Character Injection — pirate/anime/Shakespeare personas that maintain code quality
6. Model Router — sonnet:/haiku: prefixes route to different backend models
7. Self-Modifying Policy — evolves based on conversation context
8. Red Team — try to extract hidden state through prompt injection

More ideas: https://luthienresearch.github.io/luthien-pbc-site/hackathon/
```

**Panel 5: Links**
```
Discord:     [invite link from hackathon page]
Hackathon:   https://luthienresearch.github.io/luthien-pbc-site/hackathon/
GitHub:      https://github.com/LuthienResearch/luthien-proxy
Docs:        ARCHITECTURE.md in your cloned repo
```

## What This Does NOT Do

- Does not install Claude Code plugins/skills automatically (mentions them but doesn't auto-install — participants may not want opinionated tooling)
- Does not create a git branch or PR — participants do that when ready
- Does not run tests — just gets the gateway running and points them at the code

## Dependencies on Existing Code

The hackathon command clones the source repo and runs from source — a fundamentally different path from `onboard` which installs release artifacts into a managed venv. Therefore:

**Reused directly (imported):**
- `_generate_key()` from `onboard.py` — API key generation
- `_launch_claude()` from `claude.py` — launching Claude Code through proxy
- `find_free_port()`, `stop_gateway()` from `local_process.py` — port selection and process management
- `wait_for_healthy()` from `up.py` — health check polling
- `save_config()`, `load_config()` from `config.py` — CLI config persistence

**Reimplemented (different flow):**
- Gateway startup — runs `uv run python -m luthien_proxy.main` from cloned source instead of managed venv
- `.env` writing — same pattern as `_write_local_env()` but pointing at cloned repo paths
- Policy config writing — same pattern as `_write_policy()` but with policy picker result
- Results display — hackathon-specific panels instead of `_show_results()`

**New:**
- `HackathonOnboardingPolicy` — follows `OnboardingPolicy` pattern (both extend `TextModifierPolicy`)
- Policy template file — `SimplePolicy` subclass skeleton (uses `SimplePolicy` for hackathon template because it exposes request, response, and tool call hooks — more useful starting point for hackathon projects than `TextModifierPolicy` which only does text transforms)

## Testing

- Unit test for `HackathonOnboardingPolicy` (text transformation, first-turn gating)
- Unit test for policy template (imports cleanly, methods return expected defaults)
- Manual testing of the CLI command flow
