# Objective

Add `luthien hackathon` CLI command for one-command hackathon participant onboarding.

## Description

Hackathon participants need to go from zero to hacking on policies as fast as possible. The `luthien hackathon` command forks/clones the repo, installs dependencies, starts a gateway from source, creates a starter policy template, and prints a comprehensive getting-started guide with cheatsheet, UI tour, key files, and project ideas.

## Approach

1. Create `HackathonOnboardingPolicy` — TextModifierPolicy that appends hackathon welcome on first turn
2. Create `hackathon_policy_template.py` — SimplePolicy skeleton for participants to customize
3. Create `hackathon.py` CLI command — orchestrates fork/clone, deps, gateway start, policy picker, guide output
4. Register in CLI main.py

## Test Strategy

- Unit tests: HackathonOnboardingPolicy (first-turn gating, welcome content, passthrough)
- Unit tests: HackathonPolicy template (imports, passthrough defaults)
- Manual: full `luthien hackathon` flow

## Acceptance Criteria

- [ ] HackathonOnboardingPolicy with first-turn welcome
- [ ] Hackathon policy template with SimplePolicy skeleton
- [ ] `luthien hackathon` command with fork/clone, deps, gateway, policy picker, guide
- [ ] Unit tests for both policies
- [ ] dev_checks passes

## Tracking

- Trello: none
- Branch: worktree-optimized-napping-hamster
- PR: TBD
