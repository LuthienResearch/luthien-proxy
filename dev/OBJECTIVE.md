# Objective: Fix claude-review workflow for fork PRs

## Goal
Fix `claude-code-review.yml` workflow to skip PRs from forks until OAuth token is configured.

## Problem
The `claude-review` check fails on fork PRs because:
- OIDC tokens aren't available for fork PRs (GitHub security restriction)
- `CLAUDE_CODE_OAUTH_TOKEN` secret isn't configured
- Workflow tries OIDC fallback and fails

## Solution
Add conditional to skip fork PRs until OAuth token is set up by admin.

## Acceptance Criteria
- [ ] Workflow skips fork PRs
- [ ] Comment explains why and points to OAuth token solution
- [ ] Workflow still runs for same-repo PRs

## Related
- Discovered while reviewing PR #71 checks
