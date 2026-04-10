## Objective: Clean up PROXY_API_KEY vs ANTHROPIC_API_KEY auth docs

Trello: https://trello.com/c/0uZIQlD2

**Problem**: README, dev-README, and CLAUDE.md document authentication inconsistently.
The canonical source (`dev/context/authentication.md`) describes three distinct keys:

- `PROXY_API_KEY` — clients → gateway (only required in `proxy_key` auth mode)
- `ADMIN_API_KEY` — admin dashboard access (localhost bypass applies)
- `ANTHROPIC_API_KEY` — gateway → Anthropic (server-credential mode)

But README.md "Gateway Keys" implies PROXY_API_KEY is required by default, and
troubleshooting tells users to check a header that the default `luthien onboard`
flow never sets. dev-README.md collapses this into "two layers" and misses admin auth.
CLAUDE.md lists PROXY_API_KEY as a "key env var" when it's actually optional.
`.env.example` is empty (regression from a recent PR).

**Acceptance check**:
1. README, dev-README, CLAUDE.md, dev/context/authentication.md all describe the
   three auth layers consistently.
2. README troubleshooting matches what the default `luthien onboard` flow writes.
3. `.env.example` is non-empty and points at the canonical auth doc.
4. A unit test fails loudly if `.env.example` is ever generated empty again.
5. No code behavior changes.
