# Objective: README examples rework for Claude Code auto mode

Claude Code shipped **auto mode** — a Sonnet-4.6 classifier that gates tool calls and blocks `curl | bash`, force pushes, production deploys, IAM grants, mass deletes by default.

Our README's headline examples (`rm -rf`, `git push --force`, `pip install`, the three `Block*` presets listed first) live squarely in the bucket auto mode now covers. That is our weakest possible pitch to a reader who already has auto mode.

Rework README examples to:

1. Demote auto-mode-overlap bullets out of the headline use cases
2. Lead with output-verification scenarios auto mode cannot do (test-cheating, false "done" claims, CLAUDE.md rule enforcement across turns, scope boundaries)
3. Reorder Built-in Presets so output-shaping presets lead and `Block*` presets are framed as defense-in-depth for users on agents/plans without auto mode
4. (Optional) One-line positioning sentence above the bullets that says in plain words what auto mode does vs. what Luthien does

**Acceptance:** Reader who has auto mode can read the "What can it do?" section and understand what Luthien adds that auto mode does not.

**Source:** `Auto Mode Competitive Analysis` doc (gdrive 1SYhdfFvPkcJ0Z4DrrrSsE2pvyi7h5AUP) — 204-frustration corpus says security/sandboxing is 17 of ~103 mentions; the other 87+ are output-verification problems.

**Scope:** README.md only. No code changes, no test changes, no changelog fragment (doc-only).
