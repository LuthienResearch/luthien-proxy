# Objective: Chain Policy UX

Improve the chain-building experience in the policy config UI. Users should immediately understand that "chain" is how you combine multiple policies, and have clear UI for building and managing chains.

## What to Build

### 1. Recover Prior Chain UI Work

PRs #372 and #376 (superseded by #379) had chain-building UI features that were lost:
- A **plus sign icon** at the bottom of the chain for adding policies
- UI for **moving policies up/down** in the chain (reordering)
- UI for **deleting policies** from the chain

Search git history for these PRs and reference/recover what was good:
```bash
git log --all --oneline --grep="372\|376\|chain"
gh pr view 372 --json headRefName --jq .headRefName
gh pr view 376 --json headRefName --jq .headRefName
# Then look at those branches for the chain UI code
```

### 2. Chain-Building Functionality

In the config UI (`src/luthien_proxy/ui/` and `src/luthien_proxy/static/`):
- Clear way to **add policies to a chain** (plus button / drag from Available column)
- **Reorder policies** in the chain (move up/down buttons)
- **Remove policies** from the chain (delete/X button)
- Visual clarity that chain = sequential pipeline (each output feeds next input)

### 3. UX Clarity

- Make it **immediately obvious** that chaining is how you run multiple policies
- Users should NOT have to figure this out — it should be self-evident

### 4. Policy Visibility (from design session notes)

- **Hide from default view**: META policies (DebugLoggingPolicy, NoOp, PlainDashesPolicy, SimpleNoOp)
- **Hide from default view**: Multi-policy wrappers (MultiParallelPolicy, MultiSerialPolicy)
- **Rename in UI**: MultiSerialPolicy → "Chain"
- **No apologies** in copy/text

## Acceptance Criteria

- [ ] Users can add policies to a chain via clear UI affordance
- [ ] Users can reorder policies in the chain
- [ ] Users can remove policies from the chain
- [ ] It's visually obvious that "chain" = combining multiple policies
- [ ] META/internal policies are hidden from default view
- [ ] MultiSerialPolicy displays as "Chain" in the UI
- [ ] `./scripts/dev_checks.sh` passes

## Key Files to Read First

- `src/luthien_proxy/static/policy_config.js` — main config UI JavaScript
- `src/luthien_proxy/ui/` — UI templates
- `src/luthien_proxy/admin/` — admin API endpoints the UI calls
- Git history for PRs #372 and #376 — prior chain UI work
