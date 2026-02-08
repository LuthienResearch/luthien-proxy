# Luthien

**AI coding with guardrails.**

<table>
<tr>
<td>

```bash
# Two env vars. That's it.
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

</td>
<td>

Luthien is a proxy that sits between your AI coding agent and the LLM. It intercepts every request and response, letting you enforce rules, block dangerous operations, and clean up output — without changing your code or workflow.

</td>
</tr>
</table>

<!-- TODO: Replace with actual demo GIF/screenshot -->
<p align="center">
<img src="https://placehold.co/800x400/0a0a0f/6c8cff?text=30-second+demo:+Luthien+blocking+%22rm+-rf%22+and+retrying+safely" alt="Luthien demo" width="100%">
</p>

---

## Pick your policy

Luthien ships with building blocks. Combine them or write your own.

---

<details open>
<summary><h3>Block dangerous commands</h3></summary>

An LLM judge evaluates every tool call against your rules. Write rules in plain English.

```python
class SafetyPolicy(SimpleJudgePolicy):
    RULES = [
        "Never allow 'rm -rf' commands",
        "Block requests to delete production data",
        "Require approval for git push --force"
    ]
```

**What happens:** Agent tries `rm -rf dist/` → Luthien blocks it → agent retries without the dangerous command.

</details>

---

<details open>
<summary><h3>Remove AI slop</h3></summary>

Strip out em dashes, curly quotes, and other AI-isms that nobody asked for. Runs on every response as it streams through.

```python
class DeSlop(SimplePolicy):
    def simple_on_response_content(self, content, context):
        content = content.replace("\u2014", "-")   # em dash → dash
        content = content.replace("\u2013", "-")   # en dash → dash
        content = content.replace("\u201C", '"')   # curly quotes → straight
        content = content.replace("\u201D", '"')
        return content
```

> *"The M dash thing will actually work... No em dashes. $20/mo."* — Finn, Seldon Labs

</details>

---

<details>
<summary><h3>Enforce scope boundaries</h3></summary>

Prevent the agent from touching files outside the current task. Catches the "helpful refactoring" problem.

```python
class ScopeGuard(SimpleJudgePolicy):
    RULES = [
        "Only allow edits to files in src/auth/",
        "Block any import reorganization not requested",
        "Reject changes outside the current task scope"
    ]
```

> *"I want it to be blocked on stuff which is scope creepy."* — Zac, Counterweight AI

</details>

---

<details>
<summary><h3>Log everything for compliance</h3></summary>

Every request and response is stored with full conversation context. Searchable across sessions.

```python
# Built-in — just enable the database
# config/policy_config.yaml
policy:
  class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
  config: {}
# All traffic is logged regardless of active policy
```

> *"I would love to know when Claude Code thinks I'm stupid... in the reasoning traces."* — Finn, Seldon Labs

</details>

---

<details>
<summary><h3>Write your own</h3></summary>

Subclass `SimplePolicy` for transformations, `SimpleJudgePolicy` for LLM-based enforcement. Four hooks into the lifecycle:

| Hook | When it runs |
|------|-------------|
| `on_request` | Before sending to LLM |
| `on_chunk` | Each streaming chunk |
| `on_block_complete` | After a complete message/tool_use block |
| `on_response_complete` | After full response |

```python
class MyPolicy(SimplePolicy):
    def simple_on_response_content(self, content, context):
        # Your logic here
        return content
```

Restart the gateway and your policy is live. No SDK changes, no redeployment.

</details>

---

## Quick start

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env        # Add your ANTHROPIC_API_KEY
docker compose up -d
```

```bash
# Point your agent at Luthien
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
claude
```

**Dashboard:** [localhost:8000/activity/monitor](http://localhost:8000/activity/monitor) — watch requests flow through in real-time.

---

## What's running

| Service | Port | Description |
|---------|------|-------------|
| Gateway | 8000 | The proxy + dashboard + API |
| PostgreSQL | 5432 | Conversation event storage |
| Redis | 6379 | Real-time activity streaming |

---

## Who it's for

Developers who use AI coding agents daily and have been burned by:

- Scope creep ("I asked for one fix, got a full refactor")
- Ignored instructions ("Did you even read my claude.md?")
- Dangerous operations ("It tried to `rm -rf` in production")
- AI writing tics ("Em dashes — everywhere — for — no — reason")
- Lost context ("I switched tools and started from scratch")

> *"If I have to break my workflow, the friction for me to use a tool like this is a lot higher."* — Jack, Counterweight AI

---

<div align="center">

**Let AI code. Stay in control.**

Built by [Luthien Research](https://luthienresearch.org) · Open source · [Apache 2.0](LICENSE)

</div>
