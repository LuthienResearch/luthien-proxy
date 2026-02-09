<!-- v5 README draft — Feb 2026
     Options marked with ⬜ A / ⬜ B / ⬜ C for Scott to pick.
     Once choices are made, strip the option blocks and this comment. -->

---

# SECTION 1: HEADER

<!-- ────────────────────────────────────────────── -->
<!-- TAGLINE — pick one -->

> **⬜ Tagline A:**

# Luthien

### Let AI code. Stay in control.

> **⬜ Tagline B:**

# Luthien

### Rules your AI agent can't ignore.

> **⬜ Tagline C:**

# Luthien

### Make Claude Code follow your rules.

<!-- ────────────────────────────────────────────── -->
<!-- ONE-SENTENCE DESCRIPTION — pick one -->

> **⬜ Description 1** (closest to Beth Anne's favorite):

Luthien is a proxy that sits between your AI coding agent and the LLM. It intercepts every request and response, letting you enforce rules, block dangerous operations, and clean up output — without changing your code or workflow.

> **⬜ Description 2** (shorter, punchier):

Luthien sits between your AI coding agent and the LLM. Every request passes through your rules before anything executes — no code changes, no workflow changes.

> **⬜ Description 3** (lead with the action):

Point your AI coding agent at Luthien instead of the LLM. Every request and response passes through your rules — block dangerous operations, enforce standards, clean up output. Two env vars. Nothing else changes.

<!-- ────────────────────────────────────────────── -->

[See it work](#see-it-work) · [Policies](#policies) · [Quick start](#quick-start)

---

# SECTION 2: WHO IT'S FOR

<!-- ────────────────────────────────────────────── -->
<!-- WHO IT'S FOR — pick one -->

> **⬜ Who A** (responsibility-first, Jai's language):

**You use Claude Code 30+ hours a week.** You've written a detailed `claude.md`. You trust your agent to write production code. But you're responsible for what it ships — and it still:

- Touches files you didn't ask it to touch
- Runs `pip install` when your team uses `uv`
- Ignores instructions you've given it three times
- Tries `rm -rf` or `git push --force` on a bad day

You need rules it can't skip.

> **⬜ Who B** (outcomes-first):

**You're shipping production code with AI agents every day** — Claude Code, Codex, Cursor. You've invested in shaping your agent's behavior. But you've also seen it:

- Scope-creep a one-file fix into a full refactor
- Install packages with `pip` when your stack requires `uv`
- Ignore your `claude.md` and do its own thing
- Attempt destructive operations you'd never approve

Luthien gives you rules that run on every request — rules your agent can't ignore.

> **⬜ Who C** (shortest, pain-point list only):

**Built for developers who use AI coding agents daily and have been burned by:**

- Scope creep — "I asked for one fix, got a full refactor"
- Wrong tools — "It used `pip install` when we use `uv`"
- Ignored instructions — "Did you even read my `claude.md`?"
- Dangerous operations — "It tried to `rm -rf` my working directory"
- AI writing tics — "Em dashes — everywhere — for — no — reason"

---

# SECTION 3: SEE IT WORK

## See it work

<!-- TODO: Replace with actual demo GIF/video showing pip→uv or rm-rf block -->
<p align="center">
<img src="https://placehold.co/800x400/0a0a0f/6c8cff?text=DEMO:+Claude+Code+tries+pip+install+%E2%80%94+Luthien+blocks+it+%E2%80%94+suggests+uv" alt="Luthien demo — agent tries pip install, Luthien blocks and suggests uv" width="100%">
</p>

Claude Code tries to run `pip install requests`. Luthien intercepts the tool call, evaluates it against your rules, blocks it, and tells the agent to use `uv add requests` instead. The agent retries with the right command. You didn't have to do anything.

---

# SECTION 4: HOW IT WORKS

<!-- ────────────────────────────────────────────── -->
<!-- HOW IT WORKS — pick one -->

> **⬜ How A** (3 steps, minimal):

## How it works

1. **Point your agent at Luthien** — two env vars, keep your own Claude Code
2. **Write rules in Python** — plain English rules evaluated by an LLM judge, or write custom logic
3. **Luthien enforces them on every request and response** — blocks, retries, or cleans up before you see it

Nothing else changes. Your agent, your editor, your workflow — all the same.

> **⬜ How B** (diagram):

## How it works

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

Two env vars. Your agent talks to Luthien instead of the LLM directly. Every request and response passes through your policies. Nothing else changes — same agent, same editor, same workflow.

> **⬜ How C** (narrative, Jai's "without changing your dev environment"):

## How it works

Luthien is a local proxy. Instead of your agent talking directly to the LLM, it talks through Luthien. You set two environment variables and everything else stays the same — your agent, your editor, your workflow.

Every request passes through your policies before reaching the LLM. Every response passes through them again before reaching your agent. Policies are Python classes. You can write rules in plain English (evaluated by an LLM judge) or write arbitrary logic.

If a rule is violated, Luthien blocks the request, tells the agent why, and suggests an alternative. The agent retries. You don't have to intervene.

---

# SECTION 5: POLICIES

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

Every policy action is logged. You can see what got blocked, which policies fired, and how often — out of the box.

```
http://localhost:8000/activity/monitor
```

Defining a policy is the easy part. Knowing whether it's working is the hard part. Luthien stores every decision so you can measure, refine, and trust your rules over time.

---

The two base classes cover most use cases:
- **`SimpleJudgePolicy`** — write rules in plain English, an LLM evaluates them
- **`SimplePolicy`** — write Python that transforms requests or responses directly

---

# SECTION 6: QUICK START

## Quick start

```bash
# Clone and configure
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY to your real Anthropic key
```

```bash
# Start Luthien
docker compose up -d
```

```bash
# Point your agent at Luthien
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

That's it. Your Claude Code now routes through Luthien.

**What's running:**

| Service | Port | What it does |
|---------|------|-------------|
| Gateway | 8000 | The proxy — your agent talks to this |
| PostgreSQL | 5432 | Stores every request and response |
| Redis | 6379 | Powers real-time activity streaming |

*Port conflict? Change `GATEWAY_PORT` in `.env`.*

**See it in action:**

- Activity monitor: `http://localhost:8000/activity/monitor`
- Policy config: `http://localhost:8000/policy-config`

---

## Write your own policy

Create a file in `src/luthien_proxy/policies/`, restart the gateway, done:

```bash
docker compose restart gateway
```

See `src/luthien_proxy/policies/` for examples. The two base classes:

| Base class | When to use | What you write |
|-----------|------------|----------------|
| `SimpleJudgePolicy` | Rules in plain English, LLM evaluates | A `RULES` list of strings |
| `SimplePolicy` | Custom Python logic on requests/responses | Override `simple_on_request()` or `simple_on_response_content()` |

---

*[MIT License](LICENSE)*
