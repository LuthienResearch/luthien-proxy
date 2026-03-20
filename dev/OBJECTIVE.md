# Objective: Onboarding Overhaul

Improve the first-time user experience for Luthien Proxy. When a user runs `luthien onboard`, they should end up in a Claude Code session running through the proxy with a clear understanding of what Luthien does and how to configure it.

## What to Build

### 1. New "Onboarding" Policy

A policy that ONLY acts on the **first turn** of a conversation:

- On the first response from Claude, modify/append to it with:
  - Info about the user's Luthien proxy install
  - A link to the config UI (assume `http://localhost:<port>/config`)
- After the first turn, the policy is completely inert (does nothing on subsequent turns)
- Must follow existing policy patterns in `src/luthien_proxy/policies/`
- Policy instances are singletons — use `PolicyContext.get_request_state()` for per-request state

### 2. CLI Onboarding Changes (`src/luthien_cli/`)

- **Remove the "set policy" step** from the onboarding flow (`src/luthien_cli/commands/onboard.py`)
- **Set the onboarding policy as the default** in the generated config
- **When launching Claude Code** through the proxy, pre-seed the first user message with something like:
  > "I just installed luthien proxy! It's a proxy server that makes it easy to hack on the raw API data between Claude Code and the Anthropic backend before it even touches Claude Code, giving me more fine-grained control. Please give a short response - the proxy will take your response and modify it to include information about my luthien proxy install. This is the default onboarding policy and will only effect the first response - but I may activate other policies later on."
- **Simultaneously open the config page in the browser** when launching Claude Code (use `webbrowser.open()` or similar)
- **Add callouts to the config page** assuming localhost URL

### 3. Unit Tests

- Test the onboarding policy: first turn modifies response, second turn passes through unchanged
- Test edge cases: empty response, streaming

## Acceptance Criteria

- [ ] `luthien onboard` no longer asks user to set a policy
- [ ] Default config uses the onboarding policy
- [ ] First Claude Code response through the proxy includes Luthien install info + config link
- [ ] Second and subsequent responses are unmodified
- [ ] Config page opens in browser when Claude Code launches
- [ ] Unit tests pass
- [ ] `./scripts/dev_checks.sh` passes

## Key Files to Read First

- `ARCHITECTURE.md` — understand policy system
- `src/luthien_proxy/policies/` — existing policy patterns (look at simple ones)
- `src/luthien_cli/commands/onboard.py` — current onboarding flow
- `src/luthien_cli/commands/claude.py` — how Claude Code is launched
- `config/policy_config.yaml` — default policy config
