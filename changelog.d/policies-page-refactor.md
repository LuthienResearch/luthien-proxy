---
category: Features
---

**Policies page redesign with friendly names, badges, and category grouping**: The `/policy-config` admin UI is now organized around user-friendly metadata instead of raw class names.
  - Adds `display_name`, `short_description`, `badges`, `user_alert_template`, and `instructions_summary` class attributes on `BasePolicy`; populated across the relevant policy classes.
  - Available column groups policies into four categories (Simple Utilities, Active Monitoring & Editing, Fun & Goofy, Advanced/Debugging) with accordion expand/collapse.
  - Proposed column shows a single-policy-first layout with display name, badge, description, and a "User sees when policy acts:" preview for blocking policies.
  - Active column hosts the test harness with Before/After comparison.
  - Filter input now matches against display names and short descriptions in addition to raw class names.
