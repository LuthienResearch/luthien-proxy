# Luthien

### Let AI code. Stay in control.

Luthien is a proxy that sits between Claude Code (or Codex) and the LLM. It intercepts every request and response, letting you enforce rules, block dangerous operations, and clean up output — without changing your code or workflow.

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

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

- **The CTO who said "we're using Claude Code"** — and needs guardrails across the team
- **The senior engineer who wrote the `claude.md`** — and is tired of Claude Code ignoring it
- **The person who got paged when Claude Code deleted prod data** — never again

Luthien works at the layer you already manage: LLM calls and tool usage.

---

## How it works

1. **Set two env vars** — keep your IDE, your tools, your workflow
2. **Write rules in Python** — plain English evaluated by an LLM judge, or custom logic
3. **Every request and response passes through your rules** — block, retry, or clean up

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
- **Domain-specific compliance** — your internal LLM tool advises customers? Make sure it cites the right policy instead of hallucinating guidance

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

Every policy action is logged. Measure what got blocked, track false positives and false negatives, monitor latency overhead.

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
