<div align="center">

# Luthien

### Rules Claude Code can't ignore.

Set two environment variables. Keep your IDE, keep Claude Code, keep your workflow. Every request and response passes through your rules — block dangerous operations, enforce standards, clean up output.

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

</div>

---

## See it work

<!-- TODO: Replace with actual demo GIF/video -->

<table>
<tr>
<td width="50%">

### Before

<img src="https://placehold.co/500x350/1a0a0a/ff6b6b?text=%24+claude%0A%3E+Fix+login+bug%0A%0ARefactors+3+extra+files%0Aruns+pip+install%0Aruns+rm+-rf" alt="Before: Claude Code goes off-script" width="100%">

You asked to fix one bug. Claude Code refactored 3 extra files, ran `pip install` instead of `uv`, and ran `rm -rf`.

</td>
<td width="50%">

### After

<img src="https://placehold.co/500x350/0a1a0a/4ade80?text=%24+claude+(via+Luthien)%0A%3E+Fix+login+bug%0A%0ABlocked+scope+creep%0Apip+→+uv+(retried)%0ABlocked+rm+-rf%0ABug+fixed" alt="After: Luthien enforces rules" width="100%">

Luthien blocked scope creep, blocked `rm -rf`, redirected `pip install` to `uv add`. Claude retried. Clean diff.

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

**Want more autonomy from Claude Code?** Your team is already deep on it. You want to give it more freedom — but one bad tool call can't wipe out the trust you've built.

**Need to enforce rules across every LLM call?** What packages to use, what operations are allowed, what data leaves the system. Company-wide, not per-developer.

Luthien works at the layer you already own: LLM calls and tool usage.

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
