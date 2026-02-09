# Luthien

### Make Claude Code follow your rules.

Luthien sits between your AI coding agent and the LLM. Every request passes through your rules before anything executes. Every response passes through them again before reaching your agent. Block dangerous operations, enforce standards, clean up output — without changing anything about your development environment.

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

---

## See it work

<!-- TODO: Replace with actual demo GIF/video showing this exact scenario -->

<table>
<tr>
<td width="50%">

### What you asked for

> Fix the login validation bug in `src/auth/login.py`

**What the agent did without Luthien:**
- Rewrote `login.py` (good)
- Also refactored `auth_middleware.py` (didn't ask)
- Also updated 2 test files (didn't ask)
- Ran `pip install pyjwt` (team uses `uv`)
- Ran `rm -rf __pycache__` (recursive delete)

</td>
<td width="50%">

### What the agent did with Luthien

- Fixed `login.py` (good)
- Blocked: scope creep on `auth_middleware.py` — agent retried without it
- Blocked: `pip install` — agent used `uv add` instead
- Blocked: `rm -rf` — agent used targeted cleanup instead

**Result:** Clean diff. One file changed. Right package manager. No destructive commands.

</td>
</tr>
</table>

---

## Who it's for

If your team uses Claude Code for real work, someone owns one of these problems:

| | What keeps you up at night |
|---|---|
| **"Get more from AI tooling"** | You're the CTO or founding engineer who decided the team is using Claude Code. You wrote the `claude.md`. You want agents doing more — but you need guardrails before you give them more autonomy. |
| **"Make sure it doesn't break things"** | You're the senior engineer or security lead who got paged when an agent ran `rm -rf` or pushed to prod. You need rules that apply to every LLM call, company-wide. |

At most startups, that's the same person. Luthien intercepts every LLM API call and tool invocation — the layer you already own. No experience with proxies required.

---

## How it works

Luthien is a local proxy. Instead of your agent talking directly to the LLM, it talks through Luthien. You set two environment variables and everything else stays the same — your agent, your editor, your workflow.

```
Your agent ──→ Luthien proxy ──→ LLM
                    │
              Your policies
              (Python classes)
                    │
              ┌─────┴─────┐
              │ On request │ Evaluate before sending to LLM
              │ On response│ Evaluate before returning to agent
              └────────────┘
                    │
           Pass / Block / Modify
```

Every request passes through your policies before reaching the LLM. Every response passes through them again before reaching your agent. If a rule is violated, Luthien blocks the request, tells the agent why, and suggests an alternative. The agent retries. You don't have to intervene.

---

## Policies

Luthien handles the universal dangers so you can focus on your domain.

### Built-in: good defaults that ship on

Dangers every team faces. Write rules in plain English — an LLM judge evaluates every tool call against them.

**Block dangerous operations:**

- **Block dangerous operations** — `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards** — block `pip install`, suggest `uv add` instead
- **Catch PII exposure** — block responses that contain or request sensitive data
- **Flag unknown dependencies** — is this package legit?

Write rules in plain English. An LLM judge evaluates every tool call against them. 8 lines of Python — the judge does the hard work.

<details>
<summary>See what a built-in policy looks like</summary>

```python
class PipBlockPolicy(SimpleJudgePolicy):
    RULES = [
        "Block any 'pip install' or 'pip3 install' commands. Suggest 'uv add' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]
```

</details>

### Custom: your business, your rules

Anything you can define in a Python function, Luthien can enforce. This is where it gets specific to your team.

- **Clean up AI writing tics** — remove em-dashes, curly quotes, over-bulleting
- **Enforce scope boundaries** — only allow changes to files mentioned in the request
- **Domain-specific compliance** — your internal LLM tool advises customers? Make sure it references the actual policy instead of hallucinating guidance
- **Log everything for audit** — every request and response is already in PostgreSQL

<details>
<summary>See what a custom policy looks like</summary>

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

Every policy action is logged — what got blocked, which policies fired, how often. Luthien stores every decision so you can measure, refine, and build trust in your rules over time.

---

## Quick start

Two ways to get started. Both end with the same two env vars.

### Option A: Run locally

**Prerequisites:** [Docker](https://www.docker.com/) and an [Anthropic API key](https://console.anthropic.com/).

**1. Clone the repo**

`git clone https://github.com/LuthienResearch/luthien-proxy && cd luthien-proxy`

**2. Add your API key**

`cp .env.example .env` — then edit `.env` and set `ANTHROPIC_API_KEY` to your real key.

**3. Start Luthien**

`docker compose up -d`

**4. Point your agent at Luthien**

```
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

That's it. Your Claude Code now routes through Luthien.

<details>
<summary>What Docker spins up</summary>

| Service | Port | What it does |
|---------|------|-------------|
| Gateway | 8000 | The proxy — your agent talks to this |
| PostgreSQL | 5432 | Stores every request and response |
| Redis | 6379 | Powers real-time activity streaming |

Port conflict? Set `GATEWAY_PORT` in `.env`.

</details>

### Option B: Deploy to Railway (no Docker)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

Click the button. Railway provisions Postgres, Redis, and the gateway. You get a public URL in ~2 minutes. ~$5/month.

```
export ANTHROPIC_BASE_URL=https://your-app.railway.app/v1
export ANTHROPIC_API_KEY=your-proxy-api-key
claude
```

No Docker, no git clone, no local setup. Just a URL and two env vars.

---

**See it in action:** [Activity monitor](/activity/monitor) · [Policy config](/policy-config)

---

*[MIT License](LICENSE)*
