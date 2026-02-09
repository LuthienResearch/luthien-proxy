<div align="center">

# Luthien

### Rules your AI agent can't ignore.

Point your AI coding agent at Luthien instead of the LLM. Every request and response passes through your rules — block dangerous operations, enforce standards, clean up output. Two env vars. Nothing else changes.

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

</div>

---

## See it work

<!-- TODO: Replace with actual demo GIF/video -->

<table>
<tr>
<td width="50%">

### Before

<img src="https://placehold.co/400x300/1a0a0a/ff6b6b?text=%24+claude%0A%3E+Fix+the+login+bug%0A%0AClaude+refactors+3+files%0Ayou+didn%27t+ask+about.%0A%0AAlso+runs+rm+-rf+tmp/" alt="Before: agent goes rogue" width="100%">

You asked Claude to fix one bug. It refactored three files you didn't mention, installed a package with `pip` instead of `uv`, and ran `rm -rf tmp/` for good measure.

</td>
<td width="50%">

### After

<img src="https://placehold.co/400x300/0a1a0a/4ade80?text=%24+claude+(through+Luthien)%0A%3E+Fix+the+login+bug%0A%0A%E2%9B%94+Blocked+scope+creep%0A%E2%9B%94+Blocked+rm+-rf%0A%E2%9C%85+pip+%E2%86%92+uv+(auto-retry)%0A%E2%9C%85+Login+bug+fixed" alt="After: Luthien enforces rules" width="100%">

Luthien blocked the scope creep, blocked `rm -rf`, redirected `pip install` to `uv add`, and let the actual fix through. The agent retried automatically. You got a clean diff.

</td>
</tr>
</table>

<table>
<tr>
<td align="center" width="25%">

**1. You code**

Use Claude Code normally. Luthien is invisible until needed.

</td>
<td align="center" width="25%">

**2. Rules run**

Every request and response passes through your policies.

</td>
<td align="center" width="25%">

**3. Bad stuff blocked**

Dangerous ops, scope creep, wrong tools — caught before they execute.

</td>
<td align="center" width="25%">

**4. You review**

See what got blocked, what got retried, what went through.

</td>
</tr>
</table>

---

## Who it's for

**You're shipping production code with AI agents every day** — Claude Code, Codex, Cursor. You've invested in shaping your agent's behavior. But you've also seen it:

- Scope-creep a one-file fix into a full refactor
- Install packages with `pip` when your stack requires `uv`
- Ignore your `claude.md` and do its own thing
- Attempt destructive operations you'd never approve

Luthien gives you rules that run on every request — rules your agent can't ignore.

---

## Policies

Luthien handles the universal dangers so you can focus on your domain.

### Built-in: good defaults that ship on

Dangers every team faces. These ship enabled.

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

8 lines each. Rules in plain English. An LLM judge evaluates every tool call against them.

### Custom: your business, your rules

Anything you can define in Python, Luthien can enforce.

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

Every request and response is already stored in PostgreSQL. Query it, build dashboards, export for audit.

### Measurement

Every policy action is logged — what got blocked, which policies fired, how often. Defining a policy is the easy part. Knowing whether it's working is the hard part. Luthien stores every decision so you can measure and refine.

---

| Base class | When to use | What you write |
|-----------|------------|----------------|
| `SimpleJudgePolicy` | Rules in plain English, LLM evaluates | A `RULES` list of strings |
| `SimplePolicy` | Custom Python logic on requests/responses | Override `simple_on_request()` or `simple_on_response_content()` |

---

## Quick start

### Option A: Run locally

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY to your real Anthropic key
docker compose up -d
```

```bash
# Point your agent at Luthien (2 env vars, that's it)
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

**What Docker spins up:**

| Service | Port | What it does |
|---------|------|-------------|
| Gateway | 8000 | The proxy — your agent talks to this |
| PostgreSQL | 5432 | Stores every request and response |
| Redis | 6379 | Powers real-time activity streaming |

*Port conflict? Set `GATEWAY_PORT` in `.env`.*

### Option B: Deploy to cloud (no Docker needed)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

Click the button. Railway sets up Postgres, Redis, and the gateway. You get a public URL in ~2 minutes. ~$5/month.

```bash
# Point your agent at your cloud instance
export ANTHROPIC_BASE_URL=https://your-app.railway.app/v1
export ANTHROPIC_API_KEY=your-proxy-api-key
claude
```

No Docker, no git clone, no local setup. Just a URL and two env vars.

---

**See it in action:**

- Activity monitor: `http://localhost:8000/activity/monitor`
- Policy config: `http://localhost:8000/policy-config`

---

*[MIT License](LICENSE)*
