# Luthien Control

**Enforce rules on AI coding agents.** Luthien is a proxy that sits between your AI assistant (Claude Code, Codex, etc.) and the LLM backend, letting you intercept, inspect, and modify every request and response.

## What Can You Do With This?

**Write custom policies in Python** that run on every LLM interaction:

```python
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy

class MyPolicy(SimpleJudgePolicy):
    """Block dangerous commands before they execute."""

    RULES = [
        "Never allow 'rm -rf' commands",
        "Block requests to delete production data",
        "Require approval for any AWS credential access"
    ]
    # That's it! The LLM judge evaluates every request/response against your rules.
```

**Real-world use cases:**
- Block dangerous shell commands before execution
- Require human approval for sensitive operations
- Log every tool call for compliance/audit
- Replace AI-isms in responses (em-dashes, preambles)
- Enforce coding standards automatically

**Built-in features:**
- Real-time activity monitor — watch requests flow through
- Policy hot-reload — switch policies without restart
- Streaming support — works with Claude Code's streaming responses
- OpenAI & Anthropic compatible — drop-in proxy for both APIs

---

## Quick Start

**Point your AI assistant at the proxy with 2 environment variables:**

```bash
# These tell Claude Code to route through Luthien instead of directly to Anthropic
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key          # proxy auth key (not your real Anthropic key)
```

That's it. Your existing Claude Code (or any Anthropic-compatible client) now routes through Luthien.

### Start the Proxy

<details open>
<summary><b>Run locally (Docker)</b></summary>

```bash
git clone https://github.com/LuthienResearch/luthien-proxy
cd luthien-proxy
cp .env.example .env
# Edit .env: add your real ANTHROPIC_API_KEY (the upstream key Luthien uses to call Anthropic)

docker compose up -d
```

</details>

<details>
<summary><b>Deploy to cloud (Railway) — coming soon</b></summary>

One-click Railway deployment is in progress. ETA: Feb 14, 2026.

</details>

**What this starts (all in Docker):**
| Service | Port | Description |
|---------|------|-------------|
| Gateway | 8000 | The proxy — point your client here |
| PostgreSQL | 5432 | Stores conversation events |
| Redis | 6379 | Powers real-time activity streaming |
| Local LLM | 11434 | Ollama for local judge policies (optional — not needed for basic policies) |

### Verify It Works

```bash
curl http://localhost:8000/health
```

Then launch Claude Code with the env vars above and make a request. You should see it in the activity monitor:

```
http://localhost:8000/activity/monitor
```

> **First time?** Admin pages (activity monitor, policy config) require login. Default key: `admin-dev-key`

---

## Create Your Own Policy

Policies are Python classes that hook into the request/response lifecycle:

```python
# src/luthien_proxy/policies/my_custom_policy.py

from luthien_proxy.policies.simple_policy import SimplePolicy

class DeSlop(SimplePolicy):
    """Remove AI-isms from responses."""

    def simple_on_response_content(self, content: str, context) -> str:
        content = content.replace("\u2014", "-")  # em-dashes
        content = content.replace("\u2013", "-")  # en-dashes
        return content
```

For LLM-based rule enforcement, use `SimpleJudgePolicy`:

```python
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy

class SafetyPolicy(SimpleJudgePolicy):
    """Use an LLM judge to evaluate requests against rules."""

    RULES = [
        "Never execute commands that delete files recursively",
        "Block any request to access environment variables containing 'SECRET' or 'KEY'",
        "Require explicit confirmation for git push --force"
    ]
```

Restart the gateway (`docker compose restart gateway`) and your policy appears in the Policy Config UI.

**Policy lifecycle hooks:**
- `on_request` — Before sending to LLM
- `on_chunk` — Each streaming chunk (for real-time modifications)
- `on_block_complete` — After a complete message/tool_use block
- `on_response_complete` — After full response received

See `src/luthien_proxy/policies/` for more examples.

---

## What You Get

- **Gateway** (OpenAI/Anthropic-compatible) at <http://localhost:8000>
- **PostgreSQL** and **Redis** fully configured
- **Local LLM** (Ollama) at <http://localhost:11434>
- **Real-time monitoring** at <http://localhost:8000/activity/monitor>
- **Policy management UI** at <http://localhost:8000/policy-config>

## Prerequisites

- Docker
- [uv](https://docs.astral.sh/uv/) (for development)

## Further Reading

For development setup, configuration reference, architecture details, API endpoints, and troubleshooting, see **[REFERENCE.md](REFERENCE.md)**.

## License

Apache License 2.0
