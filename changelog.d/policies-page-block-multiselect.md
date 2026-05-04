---
category: Features
---

**Multi-select Block policies on the policy config page**: The three Block-type policies (Block Commands, Block File Writes, Block Web Requests) now render as a single grouped sub-section inside Active Monitoring & Editing, with checkboxes and a shared "Add selected" button.
  - New optional `group` attribute on policy classes — when 2+ policies in the same category share a `group` value, they render as a multi-select sub-group.
  - Each ticked policy still becomes its own independent entry in the chain (no compound policy concept).
  - Plumbed through `policy_discovery.py` and the `/api/admin/policy/list` response.
