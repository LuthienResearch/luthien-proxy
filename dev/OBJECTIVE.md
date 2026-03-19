# Objective

Synthesize policy config UI: merge best of PR #372 + #376 into a single cohesive policy configuration experience.

## Description

Two PRs independently redesign the policy config UI with complementary strengths:
- **PR #372** — foundations: PBC-aligned nav, landing page redesign, Simple/Advanced grouping, accessibility, Pydantic schema-driven forms
- **PR #376** — workflow: three-column Available→Proposed→Active layout, chain building, credential management, inline examples, dual test panels

This PR takes the foundations from #372 and the workflow model from #376, addressing review comments from both.

## Approach

1. **Nav + landing page foundations** (from #372): PBC-aligned nav across all pages, redesigned landing page with progressive disclosure, accessibility improvements
2. **Three-column policy config** (synthesis): Replace two-column layout with #376's Available|Proposed|Active workflow, using #372's design tokens and #372's Pydantic/Alpine.js form rendering
3. **Policy features** (from #376): Credential source dropdown, dual test panels, inline examples, chain building, settings in nav popover
4. **Progressive disclosure** (from #372): Simple/Advanced grouping in Available column, Pydantic schema-driven config forms, status indicators
5. **Backend changes**: api_key field on ChatRequest for credential testing, has_server_credentials via authenticated endpoint
6. **Address review feedback** from both PRs: fix XSS escaping, remove has_anthropic_key from unauthenticated /health, fix chain activation

## Test Strategy

- Unit tests: test ChatRequest api_key field, test gateway settings endpoint
- Smoke test: start stack, verify policy config page loads and renders correctly
- No formal e2e tests needed — this is a UI synthesis PR

## Acceptance Criteria

- [ ] Nav updated with PBC design across all pages
- [ ] Landing page redesigned with Pages + collapsible Developer Reference
- [ ] Three-column policy config with Available|Proposed|Active layout
- [ ] Simple/Advanced policy grouping in Available column
- [ ] Pydantic/Alpine.js config forms in Proposed column
- [ ] Credential source dropdown in test panels
- [ ] Dual test panels in Proposed and Active columns
- [ ] Inline policy examples on cards
- [ ] Settings in nav popover
- [ ] Chain building mode
- [ ] dev_checks passes
- [ ] Review comments from #372 and #376 addressed

## Tracking

- Trello: https://trello.com/c/aOuKzEFV
- Branch: worktree-refactored-wandering-turing
- PR: (filled after creation)
