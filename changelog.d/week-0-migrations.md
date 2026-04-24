---
category: Fixes
pr: 595
---

**Missing retention index migration**: Added migration 015 (retention index) that was omitted from PR #571
  - Migrations 014 (user_id), 015 (retention index), and 016 (session search tsvector) now complete
  - Fixes database schema consistency for data retention features
