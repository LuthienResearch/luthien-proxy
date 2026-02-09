# Luthien

### Make Claude Code follow your rules.

Luthien sits between Claude Code (or Codex) and the LLM. Every request and response passes through your rules. Block dangerous operations, enforce standards, clean up output — without changing your development environment.

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

---

## See it work

<!-- TODO: Replace with actual demo GIF/video showing this exact scenario -->

<table>
<tr>
<td width="50%">

### What you asked for

> Fix the login validation bug in `src/auth/login.py`

**What Claude Code did without Luthien:**
- Rewrote `login.py` (good)
- Also refactored `auth_middleware.py` (didn't ask)
- Also updated 2 test files (didn't ask)
- Ran `pip install pyjwt` (team uses `uv`)
- Ran `rm -rf __pycache__` (recursive delete)

</td>
<td width="50%">

### What Claude Code did with Luthien

- Fixed `login.py` (good)
- Blocked: scope creep on `auth_middleware.py` — Claude retried without it
- Blocked: `pip install` — Claude used `uv add` instead
- Blocked: `rm -rf` — Claude used targeted cleanup instead

**Result:** Clean diff. One file changed. Right package manager. No destructive commands.

</td>
</tr>
</table>

---

## Who it's for

Your team is already deep on Claude Code. Someone owns one of these problems:

| | What keeps you up at night |
|---|---|
| **"Give Claude Code more autonomy"** | You're the technical lead. Your team is shipping fast with Claude Code — but you need guardrails before giving it more freedom. |
| **"Don't let it break things"** | You got paged when Claude Code ran `rm -rf` or pushed to prod. You need rules on every LLM call, company-wide. |

Luthien works at the layer you already own: LLM calls and tool usage.

---

## How it works

Set two environment variables. Claude Code talks through Luthien instead of directly to the LLM. Everything else stays the same.

```
Claude Code ──→ Luthien proxy ──→ LLM
                    │
              Your policies
              (Python classes)
                    │
              ┌─────┴─────┐
              │ On request │ Evaluate before sending to LLM
              │ On response│ Evaluate before returning to Claude Code
              └────────────┘
                    │
           Pass / Block / Modify
```

If a rule is violated, Luthien blocks the request, tells Claude Code why, and suggests an alternative. Claude retries automatically.

---

## Policies

### Built-in: common failure modes

Ship enabled. No configuration needed.

- **Block dangerous operations** — `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards** — block `pip install`, suggest `uv add` instead
- **Catch PII exposure** — block responses that contain or request sensitive data
- **Flag unknown dependencies** — is this package legit?

Write rules in plain English. An LLM judge evaluates them.

<details>
<summary>See policy code</summary>

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

- **Clean up AI writing tics** — remove em-dashes, curly quotes, over-bulleting
- **Enforce scope boundaries** — only allow changes to files mentioned in the request
- **Domain-specific compliance** — your internal LLM tool advises customers? Make sure it references the actual policy instead of hallucinating guidance
- **Log everything for audit** — every request and response is already in PostgreSQL

<details>
<summary>See custom policy code</summary>

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

Every policy action is logged. Track what got blocked, false positives, false negatives, latency overhead.

---

## Quick start

<table>
<tr>
<td width="50%">

<details open>
<summary><b>Run locally</b></summary>

**Prerequisites:** [Docker](https://www.docker.com/) and an [Anthropic API key](https://console.anthropic.com/).

**1. Clone**

`git clone https://github.com/LuthienResearch/luthien-proxy && cd luthien-proxy`

**2. Configure**

`cp .env.example .env` — set `ANTHROPIC_API_KEY`.

**3. Start**

`docker compose up -d`

**4. Connect Claude Code**

```
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

<details>
<summary>What Docker spins up</summary>

| Service | Port | Purpose |
|---------|------|---------|
| Gateway | 8000 | Proxy endpoint |
| PostgreSQL | 5432 | Request/response storage |
| Redis | 6379 | Real-time streaming |

Port conflict? Set `GATEWAY_PORT` in `.env`.

</details>

</details>

</td>
<td width="50%">

<details>
<summary><b>Deploy to cloud</b></summary>

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

One click. Railway provisions Postgres, Redis, and the gateway. Public URL in ~2 minutes.

```
export ANTHROPIC_BASE_URL=https://your-app.railway.app/v1
export ANTHROPIC_API_KEY=your-proxy-api-key
claude
```

No Docker, no local setup. Just a URL and two env vars.

</details>

</td>
</tr>
</table>

---

**See it in action:** [Activity monitor](/activity/monitor) · [Policy config](/policy-config)

---

*[MIT License](LICENSE)*
