# Changelog Fragments

Each PR adds a fragment file here instead of editing `CHANGELOG.md` directly.
This prevents merge conflicts when multiple PRs are in flight.

## Format

**Filename:** `<PR-number>-<short-handle>.md` (e.g., `187-client-setup.md`)

**Contents:** One or more markdown bullet points describing the change:

```markdown
- Add `/client-setup` endpoint with setup guide for connecting Claude Code to the proxy
```

## Assembly

At release time, run:

```bash
uv run python scripts/assemble_changelog.py
```

This collects all fragments, inserts them into `CHANGELOG.md` under `## Unreleased`,
and deletes the fragment files.
