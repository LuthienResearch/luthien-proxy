# Session Notes: 2026-01-29

## Demo Checklist
**Location:** https://github.com/LuthienResearch/luthien-org/blob/main/demo-checklist.md

Created based on PR #134 postmortem insight: "Our demos always break. Not sometimes. Always."

## Today's Progress

1. **Fixed port bug in PR #141** - Docker port mapping was inconsistent
2. **Reverted to port 8000** - Per Jai's feedback, simpler to keep default
3. **Fixed SimplePolicy non-streaming bug** - DeSlop now works for both streaming and non-streaming
4. **Verified Codex works** - Claude Code broken due to `context_management`, but Codex works fine

## Demo-Ready State

```bash
# On onboarding-feedback branch
docker compose build gateway
docker compose up -d
# Activate DeSlop
curl -X POST http://localhost:8000/admin/policy/set \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"policy_class_ref": "luthien_proxy.policies.deslop_policy:DeSlop", "config": {}}'
```

## LiteLLM Issue (from Jai)
Claude's `context_management` parameter requires beta header. LiteLLM doesn't send it.
Jai planning to drop LiteLLM entirely.
