# Current Objective: Onboarding Improvements (PR #141)

**Branch:** `onboarding-feedback`
**PR:** https://github.com/LuthienResearch/luthien-proxy/pull/141

## Goal
Improve onboarding experience based on Zac (Counterweight), Finn, and Esben feedback.

## Acceptance Criteria
- [x] README restructured: value prop first, custom policies prominent
- [x] .env.example: clear REQUIRED vs OPTIONAL sections
- [x] DeSlop policy working (streaming + non-streaming)
- [x] Port reverted to 8000 (simpler, per Jai feedback)
- [ ] Demo to Zac successful

## Demo Prep
See: https://github.com/LuthienResearch/luthien-org/blob/main/demo-checklist.md

## Known Issues
- Claude Code may fail due to LiteLLM `context_management` bug (use Codex instead)
- Policy resets on gateway restart (re-activate after restart)
