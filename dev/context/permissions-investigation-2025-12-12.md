# GitHub Permissions Investigation - 2025-12-12

## Summary

Investigation into GitHub repository permissions for `scottwofford` on `LuthienResearch/luthien-proxy`, prompted by Jai asking if branch creation was working.

## Key Findings

### Current Permissions (Dec 12, 2025)

```bash
gh api graphql -f query='{ repository(owner: "LuthienResearch", name: "luthien-proxy") { viewerPermission } }'
# Result: {"data":{"repository":{"viewerPermission":"WRITE"}}}

gh api repos/LuthienResearch/luthien-proxy --jq '{viewerPermission: .permissions}'
# Result: {"viewerPermission":{"admin":false,"maintain":false,"pull":true,"push":true,"triage":true}}
```

**Current access level: WRITE** (includes push permissions)

### Historical Evidence

#### Nov 21, 2025 - Session `6616af7a-fd90-41e4-add0-2597e6a26e67`

- **Time:** 2:33 PM PST
- **User prompt:** "see if I have triage access on this repo?"
- **API check result:** `"viewerPermission":"TRIAGE"`
- **Conclusion:** Had TRIAGE access only

#### Dec 10, 2025 - Session `3e70058f-21b1-494a-be4e-090c3faca256`

- **Time:** 10:02 PM PST
- **Action:** `git push -u origin fix-migration-script`
- **Result:**
  ```
  Exit code 128
  remote: Permission to LuthienResearch/luthien-proxy.git denied to scottwofford.
  fatal: unable to access 'https://github.com/LuthienResearch/luthien-proxy.git/': The requested URL returned error: 403
  ```

- **Time:** 11:52 PM PST
- **Action:** Second push attempt
- **Result:** Same 403 error
- **User statement:** "I have triage access"
- **Workaround:** Used fork to create PR #91

#### Dec 12, 2025 - Current session

- **Time:** ~9:10 AM PST
- **Action:** `git push -u origin permissions_test`
- **Result:** Success
- **API check:** `viewerPermission: "WRITE"`

## Timeline

| Date | Time (PST) | Permission Level | Push Works? |
|------|------------|------------------|-------------|
| Nov 21, 2025 | 2:33 PM | TRIAGE | Not tested |
| Dec 10, 2025 | 10:02 PM | TRIAGE (inferred) | No - 403 |
| Dec 10, 2025 | 11:52 PM | TRIAGE (stated) | No - 403 |
| Dec 12, 2025 | 9:10 AM | WRITE | Yes |

## Conclusion

Permissions changed from TRIAGE to WRITE sometime between Dec 10 and Dec 12. This aligns with Jai's message asking "Are you able to make branches on the luthienresearch repo yet? That should be working" - suggesting he recently made this change.

## Open Questions

- Exact date/time of permission change (GitHub audit logs would show this, but requires admin access)
- Whether this was an intentional upgrade or fixing a configuration issue

## Source Files

Evidence extracted from Claude Code session logs:
- `~/.claude/projects/-Users-scottwofford-dev-luthien-proxy/6616af7a-fd90-41e4-add0-2597e6a26e67.jsonl`
- `~/.claude/projects/-Users-scottwofford-dev-luthien-proxy/3e70058f-21b1-494a-be4e-090c3faca256.jsonl`
