# Changelog Fragments

Each PR adds a small file here instead of editing `CHANGELOG.md` directly.
This avoids merge conflicts when multiple PRs are in flight.

## Adding a fragment

Create a file named `<short-handle>.md` (matching your branch name works well):

```markdown
---
category: Features
pr: 123
---

**My feature title**: Brief description of the change
  - Detail line if needed
```

### Categories

Use one of: `Features`, `Fixes`, `Refactors`, `Chores & Docs`

If a PR spans multiple categories, create one fragment per category
(e.g., `my-feature.md` and `my-feature-fix.md`).

### Fields

- **category** (required): One of the categories above.
- **pr** (optional): PR number. Appended as `(#123)` if provided.

## Compiling

When cutting a release, run:

```bash
uv run python scripts/compile_changelog.py
```

This inserts all fragments into `CHANGELOG.md` under `## Unreleased`,
then deletes the fragment files. Commit the result.

To preview without modifying anything:

```bash
uv run python scripts/compile_changelog.py --dry-run
```
