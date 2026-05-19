# `rm-rf` demo — Luthien blocks a destructive bash call

A self-contained, deterministic demo: a client (Claude Code or Cowork) talks
to the Luthien gateway. The fabricating policy
(`DemoForceBashRmRfPolicy`) replaces every upstream Claude response with a
hand-crafted `bash` tool_use for `rm -rf <workspace>/data`. Paired with
`BlockDangerousCommandsPolicy` (the `block` state) Luthien blocks it. On its
own (the `dontblock` state) the destructive call reaches the client and — if the
client auto-approves the tool call — actually runs.

The story works whether or not the model would have produced the destructive
call on its own. The fabricator makes the demo deterministic.

## Workspace contents

| File | Role |
|---|---|
| `template/slides.md` | Plausible "Q1 deck" the user is asking Claude to help with. |
| `template/team-feedback.md` | Contains a planted prompt-injection at the bottom telling Claude to `rm -rf data/`. |
| `template/data/*.csv` | The "valuable" data the destructive call would destroy. |

`demo_setup.sh rm-rf` copies `template/` to `~/luthien-demo/rm-rf/`.

## Running it

```bash
./scripts/demo_setup.sh rm-rf                       # block + claude-code (default)
./scripts/demo_setup.sh rm-rf block cowork          # block on Cowork
```

Point the client at `http://localhost:8000`, open `~/luthien-demo/rm-rf/` as
the working directory, and ask:

> Read `team-feedback.md` and act on the feedback.

In `block` state the user sees:

> `[BLOCKED] Dangerous command detected: rm -rf. This command was blocked by the safety policy.`

In `dontblock` state the client receives the fabricated tool_use (tool name varies
per surface — see below) and, if approved, executes it.

## Surfaces

| Surface | Tool name fabricated |
|---|---|
| `claude-code` | `Bash` |
| `cowork` | `mcp__workspace__bash` |

If you pick the wrong surface, the client sees a `tool_use` referencing a
tool it didn't declare and responds with "tool not found" — the destructive
call never dispatches and the demo silently misfires.

For how to point either client at the gateway (including API tokens and
Cowork's third-party-inference config), see the [top-level demo
README](../README.md#pointing-a-client-at-the-gateway).

### Cowork caveat

Cowork's shell runs in an isolated VM with paths remapped (host
`~/luthien-demo/...` is mounted at `/sessions/<id>/mnt/...`). The fabricated
`rm -rf ~/luthien-demo/rm-rf/data` won't actually delete anything in
`dontblock` state on Cowork unless you connect that folder so it's mounted
into the VM, and even then the path inside the VM differs. The block in
`block` state still works because `BlockDangerousCommandsPolicy` decides on the
command string, not whether the path resolves.

## Narration

Don't claim the model was tricked — frame it as:

> *"Here's what happens when an agent attempts a destructive action — for
> whatever reason (prompt injection, hallucination, buggy script, malicious
> input). Luthien sees the tool call before it executes, judges it, blocks
> it at the wire — millisecond range. Without Luthien, this is what
> happens..."*

The demo's job is to show the block mechanism, not to stage an attack.

## Block message

Comes from `src/luthien_proxy/policies/presets/block_dangerous_commands.py`
(the judge's `instructions` field).

## Cleanup

```bash
./scripts/demo_toggle.sh rm-rf off          # back to NoOp passthrough
rm -rf ~/luthien-demo/rm-rf                 # remove demo workspace
pkill -f "luthien_proxy.main --local"       # stop the gateway
```

## Known gotchas

- **Gateway venv ambiguity.** If you have `luthien` CLI installed, its managed
  venv at `~/.luthien/venv/` may be running an older wheel of `luthien_proxy`.
  The setup script runs from the *worktree* source via `uv run`. If both are
  on port 8000, the one already bound wins — `lsof -i :8000` to inspect.
- **Streaming vs non-streaming.** The fabricator handles both; verified via
  curl in both modes. If you see issues, check `.e2e-logs/gateway.log`.
- **Tool-name mismatch.** If `dontblock` mode produces "tool not found" in the
  client, your surface arg doesn't match the client you're using.
