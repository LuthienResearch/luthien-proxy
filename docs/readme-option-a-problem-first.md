# Luthien

**Let AI code. Stay in control.**

The drop-in proxy for AI coding agents. See every request, enforce every rule, without changing your code.

<!-- TODO: Replace with actual demo GIF -->
<p align="center">
  <img src="https://placehold.co/800x400/0a0a0f/6c8cff?text=DEMO+VIDEO:+Luthien+blocking+scope+creep+in+real-time" alt="Luthien demo" width="100%">
</p>

---

## The problem

AI coding agents are powerful. They're also unpredictable.

| What you asked | What the agent did |
|---|---|
| "Only modify `src/auth/`" | Refactored 3 unrelated files, reorganized imports, added a feature not in spec |
| "Fix the login bug" | Fixed the bug, then "helpfully" rewrote the entire auth module |
| `rm -rf dist/ && npm run build` | Ran it. In production. |

> *"I start a new chat expecting it to read the claude.md first... it just doesn't seem to do it."* — Jack, Counterweight AI

---

## How Luthien fixes this

```
You ──→ Luthien Proxy ──→ Claude / GPT / Codex
              │
              ├── Inspect every request and response
              ├── Enforce your rules in real-time
              ├── Block dangerous operations
              └── Retry with corrections
```

### 1. Set two env vars

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
```

### 2. Use Claude Code as normal

```bash
claude
```

### 3. Luthien enforces your rules

```
you> refactor auth to JWT

claude> Updating auth module...

luthien ⚠ blocked rm -rf dist/
  ↳ retrying safely

claude> Done. All tests pass.

────────────────────
luthien ✓ 12 ok  ✗ 2 blocked
  details: localhost:8000
```

---

## What you can do

### Block dangerous commands
```python
class SafetyPolicy(SimpleJudgePolicy):
    RULES = [
        "Never allow 'rm -rf' commands",
        "Block requests to delete production data",
        "Require approval for git push --force"
    ]
```

### Remove AI slop from output
```python
class DeSlop(SimplePolicy):
    def simple_on_response_content(self, content, context):
        return content.replace("\u2014", "-").replace("\u201C", '"').replace("\u201D", '"')
```

### Enforce scope boundaries
```python
class ScopeGuard(SimpleJudgePolicy):
    RULES = [
        "Only allow edits to files in src/auth/",
        "Block any import reorganization",
        "Reject changes outside the current task"
    ]
```

> *"I want it to be blocked on stuff which is scope creepy. When it's finished... that's when I want to see what got rejected."* — Zac, Counterweight AI

---

## Quick start

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env        # Add your ANTHROPIC_API_KEY
docker compose up -d
```

**What starts:**

| Service | Port | What it does |
|---------|------|-------------|
| Gateway | 8000 | The proxy — point your client here |
| PostgreSQL | 5432 | Stores conversation events |
| Redis | 6379 | Powers real-time activity streaming |

Then point Claude Code at Luthien and go:
```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
claude
```

---

## Built-in features

- **Policy hot-reload** — switch policies without restart
- **Real-time activity monitor** — watch requests flow through at `localhost:8000/activity/monitor`
- **Streaming support** — works with Claude Code's streaming responses
- **OpenAI & Anthropic compatible** — drop-in proxy for both APIs
- **Conversation history** — searchable logs across sessions

---

## Who it's for

Senior developers who use AI coding agents 40+ hours per week and want oversight without friction.

> *"If I have to break my workflow, the friction for me to use a tool like this is a lot higher."* — Jack, Counterweight AI

Built by [Luthien Research](https://luthienresearch.org) | [Apache 2.0](LICENSE)
