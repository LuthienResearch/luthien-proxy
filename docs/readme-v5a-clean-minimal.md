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

**You use Claude Code 30+ hours a week.** You've written a detailed `claude.md`. You trust your agent to write production code. But you're responsible for what it ships — and it still:

- Touches files you didn't ask it to touch
- Runs `pip install` when your team uses `uv`
- Ignores instructions you've given it three times
- Tries `rm -rf` or `git push --force` on a bad day

You need rules it can't skip.

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

**Block dangerous operations** — rules in plain English, evaluated by an LLM judge:

```python
class SafetyPolicy(SimpleJudgePolicy):
    RULES = [
        "Block 'rm -rf' and any recursive delete commands",
        "Block 'git push --force' to main or master",
        "Block requests to drop database tables",
    ]
```

**Enforce package standards** — your team uses `uv`, not `pip`:

```python
class PipBlockPolicy(SimpleJudgePolicy):
    RULES = [
        "Block any 'pip install' or 'pip3 install' commands. Suggest 'uv add' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]
```

These are real policies — 8 lines each. The LLM judge does the hard work. You just describe what's not allowed.

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

### Measurement

Every policy action is logged. You can see what got blocked, which policies fired, and how often — out of the box.

Defining a policy is the easy part. Knowing whether it's working is the hard part. Luthien stores every decision so you can measure, refine, and trust your rules over time.

---

The two base classes cover most use cases:
- **`SimpleJudgePolicy`** — write rules in plain English, an LLM evaluates them
- **`SimplePolicy`** — write Python that transforms requests or responses directly

---

## Quick start

### Option A: Run locally

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY to your real Anthropic key
```

```bash
docker compose up -d
```

```bash
# Point your agent at Luthien
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

**What's running:**

| Service | Port | What it does |
|---------|------|-------------|
| Gateway | 8000 | The proxy — your agent talks to this |
| PostgreSQL | 5432 | Stores every request and response |
| Redis | 6379 | Powers real-time activity streaming |

*Port conflict? Change `GATEWAY_PORT` in `.env`.*

### Option B: Deploy to cloud

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

Railway provisions everything — Postgres, Redis, and the gateway. You get a public URL in ~2 minutes.

```bash
# Point your agent at your cloud instance
export ANTHROPIC_BASE_URL=https://your-app.railway.app/v1
export ANTHROPIC_API_KEY=your-proxy-api-key
claude
```

No Docker. No git clone. Just a URL and two env vars.

---

**See it in action:**

- Activity monitor: `http://localhost:8000/activity/monitor`
- Policy config: `http://localhost:8000/policy-config`

---

## Write your own policy

Create a file in `src/luthien_proxy/policies/`, restart the gateway, done:

```bash
docker compose restart gateway
```

| Base class | When to use | What you write |
|-----------|------------|----------------|
| `SimpleJudgePolicy` | Rules in plain English, LLM evaluates | A `RULES` list of strings |
| `SimplePolicy` | Custom Python logic on requests/responses | Override `simple_on_request()` or `simple_on_response_content()` |

---

*[MIT License](LICENSE)*
