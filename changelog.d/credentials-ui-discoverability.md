---
category: Features
---

**Server credentials UI discoverability**: Surfaces the existing server-credential admin API in the UI.
  - Added a `Credentials` entry to the global nav (`static/nav.js`).
  - Added a pointer card on `/config` linking to `/credentials` for operator discoverability.
  - Added a create/list/delete section on `/credentials` wired to `POST|GET|DELETE /api/admin/credentials`.
  - First PR in a series extending Luthien toward server-side inference-provider support.
