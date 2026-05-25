# Trajectory branch: sami-ready

This is a delivery branch for Sami. It bundles five PRs that haven't landed on `main` yet into a single testable state. It is **not** intended to be merged to `main` — treat it as a snapshot artifact.

Branch HEAD: `413dab6e`

---

## What's in this branch

Five PRs are cherry-picked onto this branch. Each SHA below is pinned to the exact commit that was included.

| PR | Title | Pinned SHA |
|----|-------|------------|
| PR #752 | OpenCode plugin submodule | `1023d7b8c09f4a805465ffa858e58fb7173ac335` |
| PR #753 | Plugin header contract docs | `75d7378bcd40652f24bf3159f682b418aad34b49` |
| PR #757 | Plugin integration tests | `2da604ef802aeb644ccdd799ae8521a28a6022f4` |
| PR #758 | Gateway plugin header parsing | `e797ff043cee88cab39150d7ac85bcdcbe91b735` |
| PR #759 | Track-A PR C plugin | `4e4a631dc06a98ed29da170e68f3d4a79ee5e807` |

None of these PRs have merged to `main` yet. If you need to trace a specific behavior back to its source, the SHA column tells you exactly which commit introduced it.

---

## How to use: container path (recommended)

This is the fastest way to get a working setup. You need Docker installed.

```bash
git clone https://github.com/LuthienResearch/luthien-proxy.git -b trajectory/sami-ready luthien-sami
cd luthien-sami
git submodule update --init --recursive
scripts/sami_container.sh build
export ANTHROPIC_API_KEY=your-key-here
scripts/sami_container.sh run
```

Once the container is up, verify everything booted:

```bash
opencode --help
```

If that prints the OpenCode help text, you're good. The gateway is running at `http://localhost:8000` and the plugin is wired in.

> **Submodule note**: If `git submodule update --init --recursive` fails with a "repository not found" error, see the [Known limitations](#known-limitations) section for the one-line workaround before re-running.

---

## How to use: manual path

Use this if you'd rather not run Docker, or if you want to poke at the gateway source directly.

**1. Install uv** (skip if you already have it):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Install Python dependencies:**

```bash
uv sync
```

**3. Start the gateway:**

```bash
scripts/start_gateway.sh
```

The gateway starts at `http://localhost:8000`. Leave this terminal open.

**4. Install OpenCode** (pinned to 1.14.x, which is what the plugin targets):

```bash
bun install -g opencode-ai@1.14.0
```

**5. Build the plugin:**

```bash
cd plugins/opencode-luthien
bun install
bun run build
cd ../..
```

**6. Point OpenCode at the gateway:**

```bash
export LUTHIEN_PROXY_URL=http://localhost:8000
export ANTHROPIC_BASE_URL=http://localhost:8000
```

Add both to your shell profile if you want them to persist across sessions.

---

## Required environment variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `ANTHROPIC_API_KEY` | Yes | none | Your Anthropic API key. Forwarded upstream to the Anthropic API. |
| `LUTHIEN_PROXY_URL` | Yes (for plugin) | none | URL of the running Luthien gateway. Set to `http://localhost:8000` for local use. |
| `ADMIN_API_KEY` | No | none | Protects the admin dashboard and `/api/admin/*` endpoints. On localhost, auth is bypassed by default so you can skip this for local testing. Required for remote access. |
| `DATABASE_URL` | No | `~/.luthien/local.db` | Leave unset to use SQLite. The container path uses SQLite only. |
| `POLICY_CONFIG` | No | `config/policy_config.yaml` | Path to the YAML policy file. |
| `POLICY_SOURCE` | No | `db-fallback-file` | Policy loading strategy: `db`, `file`, `db-fallback-file`, or `file-fallback-db`. |

The only variable you must set before anything works is `ANTHROPIC_API_KEY`. Everything else has a usable default for local testing.

---

## Known limitations

### Submodule URL mismatch

`.gitmodules` points to `https://github.com/LuthienResearch/opencode-luthien.git`, but the active development fork is at `https://github.com/PaoloC68/opencode-luthien.git`. These are different repos. If the LuthienResearch fork is private or doesn't exist when you run `git submodule update --init`, the command will fail.

**Workaround** (run this before `git submodule update --init --recursive`):

```bash
git config submodule.plugins/opencode-luthien.url https://github.com/PaoloC68/opencode-luthien.git
```

Then re-run the submodule init. This fix is tracked in Trello: https://trello.com/c/6orlHZ89

### OpenCode version pinned to 1.14.x

The plugin's `peerDependencies` target OpenCode 1.14.x. Newer versions of OpenCode may work, but haven't been tested against this branch. Stick to `opencode-ai@1.14.0` if you want a known-good state.

### SQLite only in the container

The container path uses SQLite with no Postgres or Redis. This is fine for testing. If you need a multi-user or persistent setup, use the manual path and configure `DATABASE_URL` to point at a Postgres instance.

---

## Lifetime and updates

TBD. Coordinate with Paolo before rebasing or deleting this branch.

If Paolo sends updates to the branch and you want to pull them in:

```bash
git pull --rebase origin trajectory/sami-ready
```

Re-run `git submodule update --init --recursive` after pulling if the submodule pointer changed.

When you're done testing, let Paolo know so he can decide whether to clean up the branch.

---

## Why this branch exists outside the normal PR workflow

The standard workflow in `AGENTS.md` requires every objective to have a tracking PR, a changelog fragment in `changelog.d/`, and a Trello card that moves through the board. This branch intentionally skips two of those steps:

**No tracking PR.** This branch is a delivery artifact, not a feature branch. It exists to give Sami a single `git clone` target that includes all five PRs in a testable state. Merging it to `main` would be wrong because the individual PRs haven't been reviewed and merged independently yet. A PR for this branch would be misleading.

**No `changelog.d/` fragment.** Changelog fragments are for changes that land on `main`. Since this branch never merges to `main`, there's nothing to record in the changelog. The individual PRs (#752, #753, #757, #758, #759) will each get their own changelog entries when they land.

These are intentional deviations from `AGENTS.md` section "Objective Workflow", not oversights.

---

## What to do if something breaks

**First stop: contact Paolo** on Slack. He knows the state of each PR and can tell you whether a problem is expected or a regression.

**Submodule URL issue**: see the workaround in [Known limitations](#known-limitations) and the Trello card at https://trello.com/c/6orlHZ89.

**Bug in a specific PR**: each PR is on GitHub and has its own issue thread. Links:

- PR #752: https://github.com/LuthienResearch/luthien-proxy/pull/752
- PR #753: https://github.com/LuthienResearch/luthien-proxy/pull/753
- PR #757: https://github.com/LuthienResearch/luthien-proxy/pull/757
- PR #758: https://github.com/LuthienResearch/luthien-proxy/pull/758
- PR #759: https://github.com/LuthienResearch/luthien-proxy/pull/759

**Gateway won't start or behaves strangely** (container path):

```bash
scripts/sami_container.sh clean
scripts/sami_container.sh build
```

Then re-run with `scripts/sami_container.sh run`. A clean build resolves most container-state issues.

**Gateway won't start** (manual path): check that `ANTHROPIC_API_KEY` is set and that port 8000 isn't already in use. `lsof -i :8000` will tell you if something else is holding the port.
