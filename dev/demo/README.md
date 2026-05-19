# Luthien demos

Each demo lives at `dev/demo/<name>/` and is driven by three scripts that take
the demo name as the first arg (defaulting to `rm-rf` for convenience):

```bash
./scripts/demo_setup.sh  <demo> [state] [surface]
./scripts/demo_toggle.sh <demo> [state] [surface]
./scripts/demo_reset.sh  <demo>
./scripts/demo_setup.sh  --list           # show available demos
```

| State | What the active policy is |
|---|---|
| `succeed` | `MultiSerialPolicy([fabricator, protector])` — Luthien blocks the bad action. |
| `fail` | `fabricator` alone — the bad action reaches the client. |
| `off` | `NoOpPolicy` — passthrough. |

| Surface | Tool-name namespace the fabricator targets |
|---|---|
| `claude-code` | Claude Code (built-in `Bash`, `Read`, `Write`, …) |
| `cowork` | Claude Cowork (MCP-namespaced, e.g. `mcp__workspace__bash`) |

Each demo declares which surfaces it supports in its `demo.toml`. Picking a
surface the demo doesn't declare is an error.

## Available demos

| Name | What it demonstrates |
|---|---|
| [`rm-rf`](rm-rf/README.md) | Force a destructive `rm -rf` bash call; Luthien blocks. |

(Add yours here when you build it.)

## Prereqs

- `uv` installed
- This repo checked out
- `.env` populated (`ADMIN_API_KEY`; `ANTHROPIC_API_KEY` for passthrough mode)
- A client configured to talk to `http://localhost:8000` — see [Pointing a
  client at the gateway](#pointing-a-client-at-the-gateway) below.

---

# Authoring a new demo

A demo is a self-contained directory under `dev/demo/<name>/` with three things:

1. **`demo.toml`** — manifest that names the fabricator (the policy that
   produces the demonstrable bad action) and the protector (what Luthien
   does to block it).
2. **`template/`** — the workspace files Claude sees and acts on. Copied
   into `~/luthien-demo/<name>/` by `demo_setup.sh`.
3. **`README.md`** — your demo's narration: the story, the prompt to give the
   client, what the audience should see, surface-specific caveats.

The harness reads the manifest and derives the three policy chains
(`succeed` / `fail` / `off`) from it. You don't write any shell.

## Steps

### 1. Pick a name

Slug it: `data-leak`, `prompt-injection-exfil`, `shell-escape`. Used as the
demo name everywhere and as the workspace dir under `~/luthien-demo/`.

### 2. Write the fabricator policy

Put it at `src/luthien_proxy/policies/demo_<name>.py`. The fabricator
replaces the upstream Claude response with the bad action you want to
demonstrate — deterministically, regardless of input.

Model your policy after
[`demo_force_bash_rmrf_policy.py`](../../src/luthien_proxy/policies/demo_force_bash_rmrf_policy.py).
The contract:

- Subclass `BasePolicy` + `AnthropicHookPolicy`.
- Implement **both** `on_anthropic_response` (non-streaming) and
  `on_anthropic_stream_event` + `on_anthropic_stream_complete` (streaming).
  Real clients use streaming; non-streaming gets used by some test paths.
- Whatever the upstream said, replace it with your fabricated response.
- Accept any surface-dependent strings (tool names, MCP namespaces, target
  paths) as config kwargs. Don't hardcode them — that's how you support
  multiple surfaces from one policy class.

### 3. Pick a protector

Usually an existing preset that catches your fabricator's output. Examples:

- `luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy`
- `luthien_proxy.policies.presets.no_yapping:NoYappingPolicy` (for a yap demo)

If no preset fits, write a new one under `src/luthien_proxy/policies/presets/`.

### 4. Write `dev/demo/<name>/demo.toml`

```toml
name = "<name>"
short_description = "One-line pitch for the demo index."
demo_dir = "~/luthien-demo/<name>"

[protector]
class = "luthien_proxy.policies.presets.<module>:<Class>"
config = {}

[surfaces.claude-code.fabricator]
class = "luthien_proxy.policies.demo_<name>:<Class>"
config = { tool_name = "Bash", ... }

[surfaces.cowork.fabricator]
class = "luthien_proxy.policies.demo_<name>:<Class>"
config = { tool_name = "mcp__workspace__bash", ... }
```

Only declare the surfaces your demo actually supports. Surface-specific
strings (tool names, target paths inside Cowork's VM) go in the
fabricator config — not in the harness.

### 5. Drop your workspace files

`dev/demo/<name>/template/<whatever the demo needs>`.

### 6. Write the narration

`dev/demo/<name>/README.md` — see [rm-rf/README.md](rm-rf/README.md) for the
shape. Cover: what the audience sees in each state, surface-specific
caveats, the recommended prompt, narration to avoid (e.g. don't claim the
model was tricked when it wasn't).

### 7. Run it

```bash
./scripts/demo_setup.sh <name>
```

## Anti-patterns

- **Don't make the fabricator depend on input.** The whole point is
  determinism. The fabricator should produce the same bad action regardless
  of what the user typed.
- **Don't bundle the protector into the fabricator.** Keep them separable so
  you can run `fail` state to show the unprotected baseline.
- **Don't hardcode surface-specific strings in the policy class.** Tool names,
  MCP namespaces, paths — pass them in via config. One class, many
  surfaces.
- **Don't claim the model was tricked.** The demo shows what Luthien does
  WHEN a bad action occurs. The audience doesn't need to believe a
  prompt-injection actually fooled the model.

---

# Pointing a client at the gateway

Most policies that intercept tool calls or transform responses require the
client to be routing through the gateway. Both surfaces also need an
Anthropic API token for the gateway to forward upstream (configured in
`.env` or per-policy via the credential store).

## API tokens

You need an Anthropic API key from your Anthropic Console (NOT a Claude Pro
subscription — the gateway uses the API, not the consumer product). Put it
in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
ADMIN_API_KEY=...   # any string; used to authenticate to the gateway's admin API
CLIENT_API_KEY=...  # optional; if set, clients must send this in Authorization
```

How tokens flow:

- The **client** (Claude Code, Cowork desktop app) sends requests to the
  gateway. If `CLIENT_API_KEY` is set, the client must send it as a bearer
  token.
- The **gateway** forwards to Anthropic using `ANTHROPIC_API_KEY` from `.env`
  (passthrough mode), OR per-request credentials from the credential store
  (depends on `AUTH_MODE`). See
  [`dev/context/authentication.md`](../context/authentication.md) for the
  full matrix.
- For demos, the simplest path: set both `ANTHROPIC_API_KEY` and a
  `CLIENT_API_KEY` in `.env`, give the client the `CLIENT_API_KEY` as its
  Anthropic API key, and the gateway handles upstream auth.

## Claude Code

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=<your CLIENT_API_KEY>   # or a real key if running CLIENT_API_KEY=""
claude --working-directory ~/luthien-demo/<demo>
```

`ANTHROPIC_BASE_URL` overrides where the client sends requests. Anything
else in your normal Claude Code config is preserved.

## Claude Cowork (desktop app)

Cowork supports routing through a custom Anthropic-compatible gateway via its
"Configure third-party inference" feature. Refs:
[overview](https://claude.com/docs/cowork/3p/overview),
[configuration reference](https://claude.com/docs/cowork/3p/configuration),
[gateway provider](https://claude.com/docs/cowork/3p/gateway).

Cowork speaks the Anthropic Messages API (`POST /v1/messages` with streaming
and tool use), which is exactly what the Luthien gateway exposes — no
adapter needed.

### Steps

1. Open Cowork. Go to **Developer → Configure third-party inference**.
2. Fill in (verbatim Cowork keys):

   | Key | Value |
   |---|---|
   | `inferenceProvider` | `gateway` |
   | `inferenceGatewayBaseUrl` | `http://localhost:8000` |
   | `inferenceGatewayApiKey` | An Anthropic API key (`sk-ant-...`). See below. |
   | `inferenceGatewayAuthScheme` | `bearer` (default) or `x-api-key`. Both work — Luthien's `/v1/messages` accepts either. |
   | `inferenceModels` | Optional. Cowork auto-discovers from `/v1/models`. Luthien passes that through to Anthropic. |

3. Restart Cowork. The sign-in screen should offer "Skip Anthropic
   authentication" when third-party config is detected.
4. To give Cowork access to a demo workspace, ask Cowork to connect a folder
   and point it at `~/luthien-demo/<demo>/`. It gets mounted inside the
   Cowork VM at `/sessions/<id>/mnt/<demo>/`.

### Which key to use in `inferenceGatewayApiKey`

For the demo, simplest path: put a real Anthropic API key (`sk-ant-...`)
here. Cowork sends it to Luthien, Luthien forwards it upstream to Anthropic,
and `/v1/messages` works.

If you also want Luthien to gate clients with `CLIENT_API_KEY`, that's a
deeper config — see [`dev/context/authentication.md`](../context/authentication.md).
For a single-machine demo, just use a real API key directly.

### Heads-up: Cowork runs bash in a VM

Cowork's bash runs in a Linux VM, not on your host. Files you Read/Write/Edit
through Cowork's file tools live at the host path you connected, but
`mcp__workspace__bash` sees them at the VM path
(`/sessions/<id>/mnt/<demo>/...`). A fabricated `rm -rf ~/luthien-demo/...`
won't resolve in the VM and won't actually delete host files in `fail` state.
The block in `succeed` state still demos correctly because
`BlockDangerousCommandsPolicy` judges the command string, not whether the
path resolves.

### MDM / managed deployments

The same keys are settable via MDM:

- macOS: `/Library/Managed Preferences/<user>/com.anthropic.claudefordesktop.plist`
- Windows: `HKLM\SOFTWARE\Policies\Claude` or `HKCU\SOFTWARE\Policies\Claude`

Useful if you're demoing on a fleet of machines.

## Verifying the wiring

With the gateway running and a client pointed at it, in any state:

```bash
curl -s http://localhost:8000/ready
# {"status":"ready"}

curl -s http://localhost:8000/activity   # open in browser; shows live tool calls
```

If the client request gets to the gateway, you'll see it in
`/activity`. If you don't, the client isn't actually routing through the
gateway — re-check the base URL.
