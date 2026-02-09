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

**Built for developers who use AI coding agents daily and have been burned by:**

- Scope creep — "I asked for one fix, got a full refactor"
- Wrong tools — "It used `pip install` when we use `uv`"
- Ignored instructions — "Did you even read my `claude.md`?"
- Dangerous operations — "It tried to `rm -rf` my working directory"
- AI writing tics — "Em dashes — everywhere — for — no — reason"

If you use Claude Code, Codex, or Cursor 30+ hours a week and you've invested in a solid `claude.md` — but your agent still makes mistakes — Luthien gives you rules it can't skip.

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

```python
class SafetyPolicy(SimpleJudgePolicy):
    RULES = [
        "Block 'rm -rf' and any recursive delete commands",
        "Block 'git push --force' to main or master",
        "Block requests to drop database tables",
    ]
```

**Enforce package standards:**

```python
class PipBlockPolicy(SimpleJudgePolicy):
    RULES = [
        "Block any 'pip install' or 'pip3 install' commands. Suggest 'uv add' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]
```

8 lines of Python. The LLM judge does the hard work — you just describe what's not allowed.

### Custom: your business, your rules

Anything you can define in Python, Luthien can enforce. These are where it gets specific to your team.

**Clean up AI writing tics:**

```python
class DeSlop(SimplePolicy):
    """Remove em-dashes and other AI-isms from responses."""

    def simple_on_response_content(self, content: str, context) -> str:
        content = content.replace("\u2014", "-")   # em-dash
        content = content.replace("\u2013", "-")   # en-dash
        return content
```

**Enforce scope boundaries:**

```python
class ScopeGuard(SimpleJudgePolicy):
    RULES = [
        "Only allow changes to files mentioned in the original request",
        "Block creation of new test files unless tests were explicitly requested",
    ]
```

**Log everything for compliance:**

Every request and response is already stored in PostgreSQL. Query it, build dashboards, export for audit — no policy code needed.

### Measurement

Every policy action is logged — what got blocked, which policies fired, how often. Luthien stores every decision so you can measure, refine, and build trust in your rules over time.

---

| Base class | When to use | What you write |
|-----------|------------|----------------|
| `SimpleJudgePolicy` | Rules in plain English, LLM evaluates | A `RULES` list of strings |
| `SimplePolicy` | Custom Python logic on requests/responses | Override `simple_on_request()` or `simple_on_response_content()` |

---

## Quick start

Two ways to get started. Both end with the same two env vars.

### Option A: Run locally (Docker)

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY to your real Anthropic key
docker compose up -d
```

**What Docker spins up:**

| Service | Port | What it does |
|---------|------|-------------|
| Gateway | 8000 | The proxy — your agent talks to this |
| PostgreSQL | 5432 | Stores every request and response |
| Redis | 6379 | Powers real-time activity streaming |

*Port conflict? Set `GATEWAY_PORT` in `.env`.*

Then point your agent at Luthien:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

### Option B: Deploy to Railway (no Docker)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

Click the button. Railway provisions Postgres, Redis, and the gateway automatically. You get a public URL in ~2 minutes. ~$5/month.

Then point your agent at your cloud instance:

```bash
export ANTHROPIC_BASE_URL=https://your-app.railway.app/v1
export ANTHROPIC_API_KEY=your-proxy-api-key
claude
```

No Docker, no git clone, no local setup. Just a URL and two env vars.

---

**See it in action:**

- Activity monitor: `/activity/monitor`
- Policy config: `/policy-config`

---

## Write your own policy

Create a file in `src/luthien_proxy/policies/`, restart the gateway:

```bash
docker compose restart gateway
```

See the `policies/` directory for examples. Start from `SimpleJudgePolicy` (rules in plain English) or `SimplePolicy` (custom Python logic).

---

*[MIT License](LICENSE)*
