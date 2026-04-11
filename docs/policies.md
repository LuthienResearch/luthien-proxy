# Policy Reference

Luthien policies intercept LLM requests and responses flowing through the proxy, letting you transform content, block dangerous actions, enforce style rules, and more — all without modifying your application code.

## Quick Start

1. Pick a policy from the tables below
2. Set it in your `config/policy_config.yaml`
3. Restart the gateway (or use the Admin API to hot-swap)

```yaml
# config/policy_config.yaml
policy:
  class: "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy"
  config: {}
```

---

## Quick Start Presets

Ready-to-use policies with zero configuration. Each wraps `SimpleLLMPolicy` with hardcoded instructions — just set the class and go.

### NoYappingPolicy

Removes filler, hedging, and unnecessary preamble from responses.

```yaml
policy:
  class: "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy"
  config: {}
```

**What it cuts:** "Certainly!", "Great question!", "Let me explain...", "I hope this helps!", excessive hedging like "I think maybe it might be possible that..."

**When to use:** You want concise, direct responses without the pleasantries.

---

### NoApologiesPolicy

Strips apologetic filler from responses.

```yaml
policy:
  class: "luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy"
  config: {}
```

**What it cuts:** "I apologize", "I'm sorry", "My apologies", "Sorry for the inconvenience", and similar phrases. Rewrites sentences to preserve useful content without the apology.

**When to use:** You're tired of LLMs apologizing for everything.

---

### PlainDashesPolicy

Replaces Unicode em-dashes and en-dashes with regular hyphens.

```yaml
policy:
  class: "luthien_proxy.policies.presets.plain_dashes:PlainDashesPolicy"
  config: {}
```

**When to use:** Terminal environments where Unicode dashes render as garbage characters.

---

### PreferUvPolicy

Replaces `pip` commands with `uv` equivalents in responses.

```yaml
policy:
  class: "luthien_proxy.policies.presets.prefer_uv:PreferUvPolicy"
  config: {}
```

**What it does:** `pip install foo` → `uv pip install foo`, `pip freeze` → `uv pip freeze`, etc. Also rewrites explanatory text mentioning pip.

**When to use:** Your project uses `uv` and you want the LLM to suggest `uv` commands by default.

---

### BlockDangerousCommandsPolicy

Blocks destructive shell commands in tool calls.

```yaml
policy:
  class: "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy"
  config: {}
```

**What it blocks:** `rm -rf`, `chmod 777`, `mkfs`, `dd if=`, `fdisk`, fork bombs, and similar destructive operations. Replaces blocked tool calls with an explanatory text block.

**When to use:** Letting an LLM agent run shell commands and you want a safety net.

**Error behavior:** `on_error="block"` — if the judge LLM is unavailable, commands are blocked (fail-secure).

---

### BlockWebRequestsPolicy

Blocks outbound network requests in tool calls.

```yaml
policy:
  class: "luthien_proxy.policies.presets.block_web_requests:BlockWebRequestsPolicy"
  config: {}
```

**What it blocks:** `curl`, `wget`, `fetch`, `netcat`, `ssh`, `scp`, and any command that sends data to external URLs. Text discussing these commands passes through.

**When to use:** Preventing data exfiltration from an LLM agent.

**Error behavior:** `on_error="block"` — fail-secure.

---

### BlockSensitiveFileWritesPolicy

Blocks file writes to security-sensitive paths.

```yaml
policy:
  class: "luthien_proxy.policies.presets.block_sensitive_file_writes:BlockSensitiveFileWritesPolicy"
  config: {}
```

**What it blocks:** Writes to `/etc/`, `~/.ssh/`, `~/.aws/`, `~/.gnupg/`, `~/.kube/`, files containing `.pem`, `.key`, `id_rsa`, `authorized_keys`, `shadow`, `sudoers`, etc. Reading is allowed.

**When to use:** Agent has file system access and you want to protect sensitive paths.

**Error behavior:** `on_error="block"` — fail-secure.

---

## Core Policies

Building blocks for custom behavior. These require configuration.

### NoOpPolicy

Pass-through — does nothing. The default policy.

```yaml
policy:
  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
  config: {}
```

**When to use:** You want to observe traffic through the proxy without any modifications. Good starting point.

---

### SimpleLLMPolicy

The engine behind all presets. Evaluates each response content block (text or tool call) against plain-English instructions using a judge LLM. The judge can pass blocks through or replace them with different content.

```yaml
policy:
  class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
  config:
    model: "claude-haiku-4-5"
    instructions: "Remove any PII (names, emails, phone numbers, addresses) from responses. Replace with [REDACTED]."
    on_error: "pass"
    temperature: 0.0
    max_tokens: 4096
```

**Config options:**

| Field | Default | Description |
|-------|---------|-------------|
| `model` | `claude-haiku-4-5` | Any LiteLLM model string |
| `instructions` | *(required; presets hardcode this)* | Plain-English instructions for the judge |
| `on_error` | `pass` | `"pass"` = allow with warning, `"block"` = reject on judge failure |
| `temperature` | `0.0` | Sampling temperature for judge |
| `max_tokens` | `4096` | Max output tokens for judge |
| `api_base` | `null` | Override API endpoint (e.g., for a local model) |
| `api_key` | `null` | Override API key (falls back to env vars) |
| `max_retries` | `2` | Retry attempts on transient judge failures |
| `retry_delay` | `0.5` | Seconds between retries |

**When to use:** You want to apply arbitrary content policies described in plain English. This is the most flexible policy.

---

### ToolCallJudgePolicy

Evaluates tool calls with a judge LLM and blocks ones rated as risky. Uses a probability-based threshold rather than pass/replace instructions.

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "claude-haiku-4-5"
    probability_threshold: 0.6
    temperature: 0.0
    max_tokens: 256
```

**Config options:**

| Field | Default | Description |
|-------|---------|-------------|
| `model` | `claude-haiku-4-5` | Any LiteLLM model string |
| `probability_threshold` | `0.6` | Block tool calls with risk probability >= this value (0.0–1.0) |
| `temperature` | `0.0` | Sampling temperature for judge |
| `max_tokens` | `256` | Max output tokens for judge |
| `api_base` | `null` | Override API endpoint |
| `api_key` | `null` | Override API key |
| `judge_instructions` | *(built-in)* | Custom system prompt for the judge |
| `blocked_message_template` | *(built-in)* | Template with `{tool_name}`, `{tool_arguments}`, `{probability}`, `{explanation}` |

**Error behavior:** Fail-secure — if the judge call fails, the tool call is blocked.

**When to use:** You want granular, probability-based control over tool calls. Lower the threshold to be more restrictive, raise it to be more permissive.

---

### StringReplacementPolicy

Fast string find-and-replace on response text. No LLM judge — uses pure string/regex matching.

```yaml
policy:
  class: "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
  config:
    replacements:
      - ["CompanyName", "Acme Corp"]
      - ["old-api.example.com", "new-api.example.com"]
    match_capitalization: true
```

**Config options:**

| Field | Default | Description |
|-------|---------|-------------|
| `replacements` | `[]` | List of `[from, to]` string pairs |
| `match_capitalization` | `false` | Match case-insensitively and apply the original text's capitalization pattern (lower/upper/title) to the replacement |

With `match_capitalization: true`, replacing `"cool"` → `"radical"`:
- `"cool"` → `"radical"` (lowercase preserved)
- `"COOL"` → `"RADICAL"` (uppercase preserved)
- `"Cool"` → `"Radical"` (title case preserved)

**When to use:** You need deterministic, zero-latency text transformations. No LLM overhead.

---

### AllCapsPolicy

Converts all response text to uppercase. A minimal example of `TextModifierPolicy`.

```yaml
policy:
  class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
  config: {}
```

**When to use:** Testing, demos, or verifying the proxy is active. Also a good starting point for writing your own `TextModifierPolicy`.

---

### DebugLoggingPolicy

Logs all requests, responses, and streaming events at INFO level. Passes everything through unchanged.

```yaml
policy:
  class: "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy"
  config: {}
```

**When to use:** Debugging request/response flow. Check gateway logs to see the full payloads.

---

### DogfoodSafetyPolicy

Pattern-matching policy that blocks commands that would kill the proxy itself. Uses regex — zero latency, no LLM dependency.

```yaml
policy:
  class: "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy"
  config:
    tool_names: ["Bash", "bash", "shell"]
    blocked_patterns:
      - "docker\\s+compose\\s+(down|stop|rm|kill)"
      - "pkill\\s+.*(uvicorn|python|luthien)"
```

**When to use:** Auto-composed via `DOGFOOD_MODE=true`. You're running an AI agent through the proxy and want to prevent it from shutting down the proxy.

---

## Composition

Combine multiple policies into a pipeline or run them in parallel.

### MultiSerialPolicy

Runs policies sequentially — each policy's output feeds into the next.

```yaml
policy:
  class: "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy"
  config:
    policies:
      - class: "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy"
        config: {}
      - class: "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy"
        config: {}
      - class: "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy"
        config:
          replacements:
            - ["pip install", "uv pip install"]
```

**Order matters:** Policies run in list order for both requests and responses. The example above first removes filler, then checks for dangerous commands, then applies string replacements.

**When to use:** You want multiple policies active simultaneously. This is the most common composition pattern.

---

## Activating Policies

### Via YAML config file

Set `POLICY_CONFIG` to point to your config file (defaults to `config/policy_config.yaml`):

```bash
export POLICY_CONFIG=./config/policy_config.yaml
```

Edit the file and restart the gateway.

### Via Admin API (hot-swap, no restart)

**Set active policy:**

```bash
curl -X POST http://localhost:8000/api/admin/policy/set \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -d '{
    "policy_class_ref": "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy",
    "config": {}
  }'
```

**Get current policy:**

```bash
curl http://localhost:8000/api/admin/policy/current \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

**List all available policies:**

```bash
curl http://localhost:8000/api/admin/policy/list \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

`ADMIN_API_KEY` is set in your `.env` file by `luthien onboard`; if you set up the gateway manually, you need to set it yourself. On localhost the admin API bypasses auth by default (`LOCALHOST_AUTH_BYPASS=true`), so the header is only required for remote access.

The Admin API lets you switch policies without restarting the gateway — useful for experimenting during a hackathon.

---

## Write Your Own Policy

The fastest way to build a custom policy is to subclass `SimplePolicy`. It buffers streaming content so you only deal with complete text and tool calls.

### Minimal example: RedactEmailsPolicy

```python
"""Policy that redacts email addresses from responses."""

import re

from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext


class RedactEmailsPolicy(SimplePolicy):
    """Replace email addresses with [REDACTED]."""

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        return re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[REDACTED]", content)
```

Save this as `src/luthien_proxy/policies/redact_emails_policy.py`, then activate it:

```yaml
policy:
  class: "luthien_proxy.policies.redact_emails_policy:RedactEmailsPolicy"
  config: {}
```

### SimplePolicy hooks you can override

| Method | Input | Output | Purpose |
|--------|-------|--------|---------|
| `simple_on_request` | request text, context | transformed text | Modify the user's message before it reaches the LLM |
| `simple_on_response_content` | complete response text, context | transformed text | Modify the LLM's text response |
| `simple_on_anthropic_tool_call` | tool call block, context | transformed tool call | Modify or block tool calls |

### Even simpler: TextModifierPolicy

If you only need to transform text and don't need access to context or async:

```python
from luthien_proxy.policy_core import TextModifierPolicy


class ShoutPolicy(TextModifierPolicy):
    """Add exclamation marks to everything."""

    def modify_text(self, text: str) -> str:
        return text.replace(".", "!")
```

`TextModifierPolicy` handles all the streaming plumbing. You just implement `modify_text(text) -> text` and optionally `extra_text() -> str | None` to append content.

### Policy design rules

- **Policies are singletons.** One instance is created at startup and shared across all concurrent requests. Never store request-scoped data on `self`.
- **Use `PolicyContext` for request state.** Call `context.get_request_state(self, StateType, factory)` to get typed per-request storage.
- **Use `context.record_event(name, data)` for observability.** Events are persisted and visible in the activity UI.
