# Objective: One-Click Railway Deploy with OAuth Pass-Through

## Goal

Enable one-click deployment to Railway where a user presses a button, gets a running
proxy endpoint, and can immediately use it with Claude Code by setting one env var
(`ANTHROPIC_BASE_URL`).

## Requirements (from Trello card)

1. **One-click deploy**: Press a button, specify rules in English, get an endpoint
2. **OAuth pass-through**: User brings own Claude subscription, just set one env var (backend URL)
3. **Punt auth for now**: Straight pass-through, flag auth as future work
4. **Max budget on Railway**: Document Railway usage limits as safety valve
5. **Two default policies**: Logging (conversation monitoring) + English language rules

## Acceptance Criteria

- [ ] `railway.json` template provisions gateway + Postgres automatically
- [ ] Default policy config includes debug logging + SimpleLLMPolicy with English rules
- [ ] AUTH_MODE defaults to passthrough for Railway deploys
- [ ] deploy/README.md has clear one-click instructions
- [ ] User can deploy and connect Claude Code with just `ANTHROPIC_BASE_URL`
- [ ] Unit tests for policy config validity
