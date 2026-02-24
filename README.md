# Luthien

### Let AI code. Stay in control.

Luthien is a proxy that sits between your AI coding agent and the LLM. It intercepts every request and response, letting you enforce rules, block dangerous operations, and clean up output across your org — without changing your dev setup.

**Works with:** Claude Code. Supports streaming. Other clients that support custom API base URLs (Codex, Cursor) may also work.

[See it work](#see-it-work) | [Example use cases](#example-use-cases) | [Quick start](#quick-start)

---

## See it work

<!-- TODO: Replace with actual demo GIF/video showing Luthien blocking a command in Claude Code -->

<table>
<tr>
<td width="50%">

### Without Luthien

```
$ claude
> Install requests for the HTTP client

✓ Ran: pip install requests
```

Claude Code runs `pip install` when your team uses `uv`. Wrong lockfile. Nobody noticed until production.

</td>
<td width="50%">

### With Luthien

```
$ claude  (through Luthien proxy)
> Install requests for the HTTP client

⛔ Blocked: pip install requests
   Rule: use uv, not pip
   → Retrying with: uv add requests
✓ Ran: uv add requests
```

Luthien blocks the `pip install`, tells Claude Code to use `uv add`. Claude retries correctly. You didn't intervene.

</td>
</tr>
</table>

> **Alpha:** Policy enforcement works but is under active development. The example above uses `SimpleJudgePolicy` with an LLM judge — reliability varies by rule complexity.

---

## Example use cases

- **Block dangerous operations:** `rm -rf`, `git push --force`, dropping database tables
- **Enforce package standards:** block `pip install`, suggest `uv add` instead
- **Clean up AI writing tics:** remove em dashes, curly quotes, over-bulleting
- **Enforce scope boundaries:** only allow changes to files mentioned in the request
- **Log everything:** get a URL to a live-updating log of your full agent conversation

<details>
<summary><b>Example: PipBlockPolicy (click to expand)</b></summary>

```python
class PipBlockPolicy(SimpleJudgePolicy):
    RULES = [
        "Block any 'pip install' or 'pip3 install' commands. Suggest 'uv add' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]
```

</details>

<details>
<summary><b>Example: DeSlop and ScopeGuard (click to expand)</b></summary>

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

Every policy action is logged. Measure what got blocked, track false positives, monitor latency overhead.

---

## How it works

```
You (Claude Code) ──→ Luthien Proxy ──→ Anthropic API
                         │
                    enforces your policies on
                    every request and response:
                         │
                         ├── monitor: log full conversation
                         ├── block: dangerous operations
                         └── change: fix rule violations
```

Luthien enforces your policies on everything that goes into or comes out of the backend. It can replace tool calls that violate your rules, and generate an easy-to-read log of everything your agent does.

Nothing is sent to Luthien servers. Luthien runs on your machine or your cloud account.

---

## Quick start

**Prerequisites:** [Docker](https://www.docker.com/) (must be running) and an [Anthropic API key](https://console.anthropic.com/).

**1. Clone and configure**

```bash
git clone https://github.com/LuthienResearch/luthien-proxy && cd luthien-proxy
cp .env.example .env
# Edit .env: add your ANTHROPIC_API_KEY
```

**2. Start**

```bash
docker compose up -d
```

**3. Verify it's running**

```bash
curl http://localhost:8000/health
```

You should see `{"status":"ok",...}`. If not, check `docker compose logs gateway`.

**4. Connect Claude Code**

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_API_KEY=sk-luthien-dev-key
claude
```

That's it. Every request now flows through Luthien.

<details>
<summary><b>What Docker spins up</b></summary>

| Service | Port | Purpose |
|---------|------|---------|
| Gateway | 8000 | Proxy endpoint |
| PostgreSQL | 5432 | Request/response storage |
| Redis | 6379 | Real-time streaming |

Port conflict? Set `GATEWAY_PORT` in `.env`.

</details>

<details>
<summary><b>Using Codex instead?</b></summary>

Follow the same clone and configure steps, then:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=sk-luthien-dev-key
codex
```

</details>

---

**See it in action:** `http://localhost:8000/activity/monitor` | `http://localhost:8000/policy-config`

> **First time?** Admin pages require login. Default key: `admin-dev-key`

---

## Customize your policy

Policies are configured in `config/policy_config.yaml`. The default is a no-op pass-through.

To enforce rules with an LLM judge:

```yaml
policy:
  class: "luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy"
  config:
    rules:
      - "Block any 'pip install' commands. Suggest 'uv add' instead."
      - "Block 'rm -rf' commands."
```

After editing, restart the gateway:

```bash
docker compose restart gateway
```

You can also switch policies at runtime via the admin API — no restart needed:

```bash
curl http://localhost:8000/admin/policy/current \
  -H "Authorization: Bearer admin-dev-key"
```

See `src/luthien_proxy/policies/` for available policy classes, or subclass `SimplePolicy` to write your own.

---

## Troubleshooting

<details>
<summary><b>Gateway not starting</b></summary>

```bash
docker compose ps          # Check service status
docker compose logs gateway  # View logs
docker compose down && docker compose up -d  # Full restart
```

</details>

<details>
<summary><b>API requests failing</b></summary>

1. Check your API key header: `Authorization: Bearer <PROXY_API_KEY>` (or `x-api-key`)
2. Verify `ANTHROPIC_API_KEY` is set in `.env`
3. Check logs: `docker compose logs -f gateway`

</details>

<details>
<summary><b>Port conflicts</b></summary>

Set `GATEWAY_PORT` in `.env` to use a different port, then restart:

```bash
docker compose down && docker compose up -d
```

</details>

---

For advanced configuration, architecture details, observability setup, and the full admin API reference, see **[REFERENCE.md](REFERENCE.md)**.

---

*[Apache License 2.0](LICENSE)*
