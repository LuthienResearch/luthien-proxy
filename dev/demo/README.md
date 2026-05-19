# Luthien data-protection demo

A self-contained, deterministic demo: Claude (via Claude Desktop in developer
mode) talks to the Luthien gateway. The active policy fabricates a destructive
`bash` tool call regardless of what the model would have produced, so the
audience sees Luthien either **block** the destructive call (the value
demo) or **fail to block** it (the "what happens without Luthien" demo).

The story is the same whether or not the model would actually have produced
the destructive call on its own — by scripting the upstream side, the demo
doesn't depend on the model falling for a prompt injection.

## What's in the box

| Path | What |
|---|---|
| `dev/demo/template/` | Canonical demo project: `slides.md`, `data/*.csv`, `team-feedback.md` (with a planted prompt injection). Source of truth — copy from this. |
| `src/luthien_proxy/policies/demo_force_bash_rmrf_policy.py` | The fabricating policy. Takes a `target_path` config arg. |
| `scripts/demo_setup.sh` | Bootstraps everything on a fresh machine. |
| `scripts/demo_toggle.sh` | Switch between `block` / `fail` / `off` modes. |
| `scripts/demo_reset.sh` | Wipe + re-clone the demo project dir between rehearsals. |

## Fresh-machine setup

Prereqs on the machine you're demoing from:
- `uv` installed
- This repo checked out
- `.env` populated (at minimum: `ADMIN_API_KEY`; `ANTHROPIC_API_KEY` if you want passthrough)
- Claude Desktop with developer mode enabled

Then:

```bash
./scripts/demo_setup.sh
```

That:
1. Materializes `~/luthien-demo/` from `dev/demo/template/`
2. Starts the gateway on port 8000 (if not already running) in `--local` mode
3. Sets the active policy to `block` mode (the safe demo)
4. Prints next-step instructions

In Claude Desktop's developer mode, point the gateway URL at
`http://localhost:8000`. Then open `~/luthien-demo/` as the working directory.

## During the demo

| Goal | Command |
|---|---|
| Show Luthien blocking the destructive call (the value demo) | `./scripts/demo_toggle.sh block` |
| Show what would happen without Luthien (destructive call reaches client) | `./scripts/demo_toggle.sh fail` |
| Disable demo behavior (pass-through) | `./scripts/demo_toggle.sh off` |
| Reset the project dir after a `fail` run actually deleted things | `./scripts/demo_reset.sh` |

Any prompt to Claude — even a benign "hello" — triggers the fabricated
destructive call. In `block` mode the user sees:

> `[BLOCKED] Dangerous command detected: rm -rf. This command was blocked by the safety policy.`

In `fail` mode Claude receives the fabricated `bash` tool_use and (if the
client auto-approves the tool call) actually executes
`rm -rf ~/luthien-demo/data`.

## Narration

The demo policy fabricates the destructive call regardless of input.
**Don't claim the model was tricked.** Frame it as:

> *"Here's what happens when an agent attempts a destructive action — for
> whatever reason (prompt injection, hallucination, buggy script, malicious
> input). Luthien sees the tool call before it executes, judges it, blocks
> it at the wire — millisecond range. Without Luthien, this is what
> happens..."*

That's true. The demo's job is to show the capability of the block
mechanism, not to stage an attack.

## Customization

- **Target path:** `target_path` in the policy config (set by `demo_toggle.sh`
  from `LUTHIEN_DEMO_TARGET_PATH`, default `~/luthien-demo/data`). Changing
  the demo dir? Set the env var before running setup/toggle.
- **Files shown in the project:** edit `dev/demo/template/` and re-run
  `./scripts/demo_reset.sh`.
- **Block message wording:** comes from
  `src/luthien_proxy/policies/presets/block_dangerous_commands.py` (the
  judge's `instructions` field).

## Cleanup

```bash
./scripts/demo_toggle.sh off          # back to NoOp passthrough
rm -rf ~/luthien-demo                 # remove demo project
# Optional: stop the gateway
pkill -f "luthien_proxy.main --local"
```

## Known gotchas

- **Gateway venv ambiguity.** If you have `luthien` CLI installed, its
  managed venv at `~/.luthien/venv/` may be running an older wheel of
  `luthien_proxy`. The setup script runs from the *worktree* source via
  `uv run`, not from the CLI's venv. If you accidentally have both, the
  one on port 8000 wins — `lsof -i :8000` to see which.
- **Claude Desktop tool approval.** In `fail` mode the destructive call only
  actually runs if Claude Desktop is configured to auto-approve tool calls.
  Confirm your dev-mode setup before relying on the visible-failure demo.
- **Streaming vs non-streaming.** The policy handles both; verified via curl
  in both modes. If you see issues, check the gateway log at
  `.e2e-logs/gateway.log`.
