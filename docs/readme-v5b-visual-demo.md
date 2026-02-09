<div align="center">

# Luthien

### Rules your AI agent can't ignore.

Set two environment variables. Keep your IDE, keep your agent, keep your workflow. Now every request and response passes through your rules — block dangerous operations, enforce standards, clean up output.

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

Two kinds of people want this:

**You're accelerating AI tooling at your company.** You decided your team is using Claude Code. You're writing the `claude.md`, picking the models, pushing adoption. You want more output from agents — but you need guardrails so one bad tool call doesn't wipe out the trust you've built.

**You're making sure AI tools don't cause problems.** Your team is already using agents in production. You need to enforce company policies across every LLM call — what packages to use, what operations are allowed, what data can't leave the system.

At a startup, you're probably the same person. Luthien works at the layer you already own: every LLM API call and every tool the agent tries to use.

---

## Policies

Luthien handles the universal dangers so you can focus on your domain.

### Built-in: good defaults that ship on

Dangers every team faces. These ship enabled.

- **Block dangerous operations** — `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards** — block `pip install`, suggest `uv add` instead
- **Catch PII exposure** — block responses that contain or request sensitive data
- **Flag unknown dependencies** — is this package legit?

Write rules in plain English. An LLM judge evaluates every tool call against them.

<details>
<summary>See what a policy looks like (8 lines)</summary>

```python
class PipBlockPolicy(SimpleJudgePolicy):
    RULES = [
        "Block any 'pip install' or 'pip3 install' commands. Suggest 'uv add' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]
```

That's the entire policy. The LLM judge does the hard work.

</details>

### Custom: your business, your rules

Anything you can define in a Python function, Luthien can enforce.

- **Clean up AI writing tics** — remove em-dashes, curly quotes, over-bulleting
- **Enforce scope boundaries** — only allow changes to files mentioned in the request
- **Domain-specific compliance** — your internal LLM tool advises customers? Make sure it cites the right policy instead of hallucinating guidance
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

Every policy action is logged — what got blocked, which policies fired, how often. Luthien stores every decision so you can measure, refine, and trust your rules over time.

---

## Quick start

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

### Option B: Deploy to cloud (no Docker needed)

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
