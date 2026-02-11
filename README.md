# Luthien

### Let AI code. Stay in control.

Luthien is a proxy that sits between your AI coding agent and the LLM. It intercepts every request and response, letting you enforce rules, block dangerous operations, and clean up output without changing your dev setup.

**Works with:** Claude Code, Codex, Cursor. Supports streaming.
**Does not work with:** Windsurf (does not support custom proxy servers).

[See it work](#see-it-work) | [What policies can do](#what-policies-can-do) | [Quick start](#quick-start) | [Security](#security-and-trust)

---

## See it work

<!-- TODO: Replace with actual demo GIF/video -->

<table>
<tr>
<td width="50%">

### Without Luthien

<img src="https://placehold.co/500x300/1a0a0a/ff6b6b?text=pip+install+requests%0ATeam+uses+uv%0ALockfile+broken" alt="Before: wrong package manager" width="100%">

Claude Code runs `pip install` when your team uses `uv`. Wrong lockfile. Nobody noticed until production.

</td>
<td width="50%">

### With Luthien

<img src="https://placehold.co/500x300/0a1a0a/4ade80?text=Blocked+pip+install%0ASuggests+uv+add%0AClaude+retries+correctly" alt="After: Luthien blocks pip, suggests uv" width="100%">

Luthien blocks the `pip install`, tells Claude Code to use `uv add`. Claude retries correctly. You didn't intervene.

</td>
</tr>
</table>

---

## Who it's for

| | |
|---|---|
| **You use AI coding agents daily** | You've seen them delete files, install wrong packages, and ignore your project rules. You know the failure modes; you want rules that actually stick. |
| **You own AI coding policy for your org** | You provision API keys for devs, own the team's config files, and ensure use complies with company policy. |

Luthien works at the layer you already manage: LLM calls and tool usage.

---

## How it works

1. **Set two env vars:** keep your IDE, your tools, your workflow
2. **Write rules in Python:** plain English evaluated by an LLM judge, or custom logic
3. **Every request and response passes through your rules:** block, retry, or clean up

Nothing is sent to Luthien servers. See [Security and trust](#security-and-trust).

---

## What policies can do

### Built-in: common failure modes

- **Block dangerous operations:** `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards:** block `pip install`, suggest `uv add` instead
- **Catch PII exposure:** block responses that contain or request sensitive data
- **Flag unknown dependencies:** is this package legit?

Write rules in plain English. An LLM judge evaluates them.

> **Expand the sections below** to see example policy code.

<details>
<summary><b>Example: PipBlockPolicy (click to expand)</b></summary>

```python
class PipBlockPolicy(SimpleJudgePolicy):
    RULES = [
        "Block any 'pip install' or 'pip3 install' commands. Suggest 'uv add' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]
```

</details>

### Custom policies for your use case

Anything you can define in a Python function.

- **Clean up AI writing tics:** remove curly quotes, over-bulleting, sloppy formatting
- **Enforce scope boundaries:** only allow changes to files mentioned in the request
- **Domain-specific compliance:** your internal LLM tool advises customers? Make sure it cites the right policy instead of hallucinating guidance

<details>
<summary><b>Example: DeSlop and ScopeGuard (click to expand)</b></summary>

```python
class DeSlop(SimplePolicy):
    def simple_on_response_content(self, content, context):
        return content.replace("\u2014", "-").replace("\u2013", "-")
```

Or use the LLM judge with your own rules:

```python
class ScopeGuard(SimpleJudgePolicy):
    RULES = [
        "Only allow changes to files mentioned in the original request",
        "Block creation of new test files unless tests were explicitly requested",
    ]
```

</details>

### Measurement

Every policy action is logged. Measure what got blocked, track false positives and false negatives, monitor latency overhead.

---

## Security and trust

Luthien runs on **infrastructure you control**: your machine or your cloud account. Your code, prompts, and API keys never touch our servers.

| Concern | How Luthien handles it |
|---------|----------------------|
| **Data transmission** | All traffic stays between you and your LLM provider. Luthien doesn't phone home. |
| **Encryption at rest** | Conversation logs are stored in your PostgreSQL instance. Encrypt the volume to your standards. |
| **Prompts and context** | Your system prompts, tool calls, and context files stay on your infrastructure. Luthien sees the traffic to enforce rules; it doesn't exfiltrate it. |
| **What Luthien sees** | Everything your AI agent sends and receives. |

---

## Quick start

**Supported:** Claude Code, Codex, Cursor (any client that lets you set a custom API base URL).

### Run locally

**Prerequisites:** [Docker](https://www.docker.com/) and an [Anthropic API key](https://console.anthropic.com/).

**1. Clone**

`git clone https://github.com/LuthienResearch/luthien-proxy && cd luthien-proxy`

**2. Configure**

`cp .env.example .env` then add your real `ANTHROPIC_API_KEY` (the upstream key Luthien uses to call Anthropic).

**3. Start**

`docker compose up -d`

**4. Connect your AI coding agent**

```bash
# These tell your agent to route through Luthien instead of directly to Anthropic
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key          # proxy auth key (not your real Anthropic key)
claude
```

<details>
<summary><b>What Docker spins up (click to expand)</b></summary>

| Service | Port | Purpose |
|---------|------|---------|
| Gateway | 8000 | Proxy endpoint |
| PostgreSQL | 5432 | Request/response storage |
| Redis | 6379 | Real-time streaming |

Port conflict? Set `GATEWAY_PORT` in `.env`.

</details>

### Deploy to cloud (Railway)

ETA Feb 14, 2026.

---

**See it in action:** [Activity monitor](/activity/monitor) | [Policy config](/policy-config)

> **First time?** Admin pages require login. Default key: `admin-dev-key`

For configuration, architecture, API endpoints, and troubleshooting, see **[REFERENCE.md](REFERENCE.md)**.

---

*[Apache License 2.0](LICENSE)*
