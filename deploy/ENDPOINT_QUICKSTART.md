# Luthien Endpoint - Quick Start

**Live Endpoint:** `https://luthien-proxy-production-0b7d.up.railway.app`
**Client API Key:** `05d5eb63457fdb9cdfd6b4c21eede816068606451be7836fdd86d500085ede25`

---

## Connect Your Agent

### Claude Code
```bash
export ANTHROPIC_BASE_URL=https://luthien-proxy-production-0b7d.up.railway.app/
export ANTHROPIC_AUTH_TOKEN=05d5eb63457fdb9cdfd6b4c21eede816068606451be7836fdd86d500085ede25
claude
```

### Codex (OpenAI format)
```bash
export OPENAI_API_BASE=https://luthien-proxy-production-0b7d.up.railway.app/v1
export OPENAI_API_KEY=05d5eb63457fdb9cdfd6b4c21eede816068606451be7836fdd86d500085ede25
```

---

## Test Connection

```bash
# Health check
curl https://luthien-proxy-production-0b7d.up.railway.app/health

# Test completion
curl https://luthien-proxy-production-0b7d.up.railway.app/v1/chat/completions \
  -H "Authorization: Bearer 05d5eb63457fdb9cdfd6b4c21eede816068606451be7836fdd86d500085ede25" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]}'
```

---

## View Activity

- **Activity Monitor:** https://luthien-proxy-production-0b7d.up.railway.app/activity/monitor
- **Conversation History:** https://luthien-proxy-production-0b7d.up.railway.app/history
- **Policy Config:** https://luthien-proxy-production-0b7d.up.railway.app/policy-config
- **Diff Viewer:** https://luthien-proxy-production-0b7d.up.railway.app/debug/diff *(admin key required)*
