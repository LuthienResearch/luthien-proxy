# Luthien

### Let AI code. Stay in control.

Luthien is a proxy that sits between your AI coding agent and the LLM. It intercepts every request and response, letting you enforce rules, block dangerous operations, and clean up output — without changing your code or workflow.

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

---

## See it work

<!-- TODO: Replace with actual demo GIF/video -->

<table>
<tr>
<td width="50%">

### Without Luthien

<img src="https://placehold.co/400x250/1a0a0a/ff6b6b?text=Claude+runs+pip+install+requests%0AYour+team+uses+uv%0ABreaks+your+lockfile" alt="Before: wrong package manager" width="100%">

Your agent runs `pip install` when your team uses `uv`. It installs packages you didn't ask for. Your lockfile is wrong and nobody noticed until production.

</td>
<td width="50%">

### With Luthien

<img src="https://placehold.co/400x250/0a1a0a/4ade80?text=Luthien+blocks+pip+install%0ASuggests+uv+add%0AAgent+retries+correctly" alt="After: Luthien blocks pip, suggests uv" width="100%">

Luthien intercepts the `pip install`, blocks it, tells the agent to use `uv add` instead. The agent retries with the right command. You didn't have to do anything.

</td>
</tr>
</table>

---

## Who it's for

You use Claude Code for production work. You're also the person who decides how your team adopts AI tooling — or the person who has to make sure it doesn't cause problems. Maybe both.

- **The CTO who said "we're using Claude Code"** — and now needs guardrails across the team
- **The senior engineer who wrote the company's `claude.md`** — and is tired of the agent ignoring it
- **The person who got paged when the agent deleted prod data** — and never wants that again

Luthien works at the layer you already manage — LLM calls and tool usage. If your team is making API calls to Claude or OpenAI, Luthien can enforce rules on every one of them.

---

## How it works

1. **Point your agent at Luthien** — two env vars, keep your own Claude Code
2. **Write rules in Python** — plain English rules evaluated by an LLM judge, or write custom logic
3. **Luthien enforces them on every request and response** — blocks, retries, or cleans up before you see it

Nothing else changes. Your agent, your editor, your workflow — all the same.

---

## Policies

Luthien handles the universal dangers so you can focus on your domain.

### Built-in: good defaults that ship on

Dangers every team faces. These ship enabled — you don't have to configure anything.

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

That's the entire policy. The LLM judge does the hard work — you just describe what's not allowed.

</details>

### Custom: your business, your rules

Anything you can define in a Python function, Luthien can enforce.

- **Clean up AI writing tics** — remove em-dashes, curly quotes, over-bulleting
- **Enforce scope boundaries** — only allow changes to files mentioned in the request
- **Domain-specific compliance** — your internal LLM tool advises customers? Make sure it cites the right policy instead of hallucinating guidance

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

### Option B: Deploy to cloud

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

Click the button. Railway provisions Postgres, Redis, and the gateway. You get a public URL in ~2 minutes. ~$5/month.

Then point your agent at your cloud instance:

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
