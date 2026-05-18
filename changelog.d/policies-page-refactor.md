---
category: Features
---

**Policies page redesign with friendly names, badges, and category grouping**: The `/policy-config` admin UI is now organized around user-friendly metadata instead of raw class names.
  - Adds a `ui` class attribute on `BasePolicy` (a `UIMetadata` frozen dataclass) carrying `display_name`, `short_description`, `category`, `catalog_badges`, and `ui_policy_preview`. `Category` and `CatalogBadge` are typed `StrEnum`s so allowed values are checked at type-check time. The `ui` attribute is structurally isolated from runtime concerns — UI-only, no execution effect.
  - Available column groups policies into four categories (Simple Utilities, Active Monitoring & Editing, Fun & Goofy, Advanced/Debugging) with accordion expand/collapse.
  - Proposed column renders the policy's actual judge prompt directly from `example_config.instructions` (no separate, drift-prone summary field). Blocking policies show a `ui_policy_preview` chip clearly labeled "Policy preview (production output may differ)".
  - Active column hosts the test harness with Before/After comparison.
  - Filter input now matches against display names and short descriptions in addition to raw class names.
