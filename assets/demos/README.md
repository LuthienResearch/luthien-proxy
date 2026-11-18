# Demo recordings

Scripts that render the Luthien demo videos (install, with/without contrast,
CLI policy tour, Claude Code TUI, and browser UI clips).  Only the *recipes*
live in this repo.  Rendered video never does.

Originally built by Jai Dhyani in
[#723](https://github.com/LuthienResearch/luthien-proxy/pull/723); that PR's
draft `.mp4` clips were intentionally left out of git.

## Where rendered videos live

Rendered output (`*.mp4`, `*.gif`, `*.webm`, `*.mov`) is gitignored anywhere
under `assets/demos/` (see `.gitignore` here and in `raw/`).  Don't
force-add it.

- **Raw clips** land in `assets/demos/raw/` on the machine that rendered
  them.  They're disposable: regenerate them from the scripts below.
- **Finished cuts** are published outside this repo:
  - for the website, in the site repo
    [LuthienResearch/luthien-pbc-site](https://github.com/LuthienResearch/luthien-pbc-site)
    (luthien.cc), or
  - for the project README or sharing, as an asset on a
    [GitHub release](https://github.com/LuthienResearch/luthien-proxy/releases)
    (`gh release upload <tag> <file>.mp4`), then linked by URL.

Keep lightweight static images (SVG) in `assets/readme/` as today; link
videos by URL instead of committing them.

## Layout

```
assets/demos/
  README.md                    # this file
  .gitignore                   # blocks rendered video/gif anywhere here
  regen.sh                     # render all (or one) VHS tapes
  mock_runtime.py              # mock Anthropic backend + reconfigured gateway
  install.tape                 # VHS: curl|bash install + onboard
  without-luthien.tape         # VHS: claude -p straight to the mock, no proxy
  with-luthien-prefer-uv.tape  # VHS: same prompt through the proxy, pip -> uv pip
  policy-in-action.tape        # VHS: CLI tour, policy list / set / current
  interactive-claude.tape      # VHS: full Claude Code TUI through the proxy
  record_policy_config.py      # Playwright: /policy-config UI clip
  record_history.py            # Playwright: /history UI clip
  shots/                       # storyboards for the browser clips
  raw/                         # gitignored render output
```

## Tooling

```bash
brew install vhs ffmpeg
uv run --with playwright playwright install chromium   # once, for the browser clips
```

## Regenerating the videos

1. **Boot the mock runtime in one terminal** (needed by the with/without,
   interactive, and Playwright clips):

   ```bash
   uv run python assets/demos/mock_runtime.py
   ```

   This:
   - starts a `MockAnthropicServer` on port 18888 (reused from
     `tests/luthien_proxy/e2e_tests/mock_anthropic/`),
   - backs up and rewrites `~/.luthien/luthien-proxy/.env` so the gateway's
     upstream (`ANTHROPIC_BASE_URL`) is the mock,
   - restarts the gateway via `luthien down && luthien up`,
   - stores a fake `anthropic` server credential,
   - activates `StringReplacementPolicy` rewriting `pip install` to
     `uv pip install` on responses,
   - seeds the mock response queue so output is deterministic.

   Leave it running.  Ctrl-C tears down: restores the original `.env`,
   restarts the gateway, stops the mock.  Re-seed between takes:

   ```bash
   kill -USR1 $(pgrep -f mock_runtime.py)
   ```

2. **Render the terminal clips** in a second terminal:

   ```bash
   assets/demos/regen.sh                 # every tape
   assets/demos/regen.sh prefer-uv       # tapes whose path contains "prefer-uv"
   ```

   Each tape's header lists its pre-conditions; `regen.sh` does not reset
   state between tapes.

3. **Render the browser clips** (gateway running; defaults to
   `http://localhost:8000`, override with `LUTHIEN_GATEWAY_URL`):

   ```bash
   uv run --with playwright python assets/demos/record_policy_config.py
   uv run --with playwright python assets/demos/record_history.py
   ```

   Each writes `raw/<name>.webm` and converts it to `raw/<name>.mp4`.

4. **Install clip needs the real network** (`install.tape`): stop the mock
   runtime first and reset the machine
   (`uv tool uninstall luthien-cli && rm -rf ~/.luthien`).

## Tapes

| Tape | Output | Mock runtime? | Pre-conditions |
|---|---|---|---|
| `install.tape` | `raw/install.mp4` | No | luthien-cli NOT installed |
| `without-luthien.tape` | `raw/without-luthien.mp4` | Yes | tape points `claude -p` straight at the mock |
| `with-luthien-prefer-uv.tape` | `raw/with-luthien-prefer-uv.mp4` | Yes | seeded queue |
| `interactive-claude.tape` | `raw/interactive-claude.mp4` | Yes | Claude Code already past first-launch setup |
| `policy-in-action.tape` | `raw/policy-in-action.mp4` | No (gateway must be running) | any active policy |

## With/without contrast

`without-luthien.tape` and `with-luthien-prefer-uv.tape` use the identical
prompt: "How do I install the requests library? Give me one shell command."
Without the proxy: `pip install requests`.  With the proxy and the
string-replacement policy: `uv pip install requests`.  Same theme, font, and
width, so they cut together side by side:

```bash
ffmpeg -i raw/without-luthien.mp4 -i raw/with-luthien-prefer-uv.mp4 \
       -filter_complex hstack -c:v libx264 -crf 20 -preset slow \
       -movflags +faststart -an raw/contrast-side-by-side.mp4
```

## Why mock the backend

- **Free**: no real Anthropic API calls.
- **Deterministic**: identical output every render.  Real Claude varied its
  formatting, which broke the side-by-side cut.
- **No auth setup**: the mock doesn't check credentials.

`StringReplacementPolicy` is used instead of a judge policy
(`SimpleLLMPolicy` / `PreferUvPolicy`) because interactive Claude Code sends
extra requests that consumed queued judge responses out of order.  Demoing a
judge specifically is a separate take with explicit queue management.

## Why VHS and Playwright

VHS scripts are reproducible: when CLI output changes, re-run the tape and the
clip updates.  Playwright records its own browser frames (no macOS Screen
Recording permission) and click sequences are code, not pixel coordinates.
The `shots/` storyboards describe the intended beats if you'd rather capture
the browser manually (QuickTime, then `ffmpeg` to MP4).

## Known gaps (TODO)

Spot-checked against `main` on 2026-09-27.  Confirmed still current:
`luthien claude`, `luthien policy list|set|current`, `luthien down|up`,
`luthien onboard` ("Continue? [Y/n]" and the "q to quit" prompt), the
`https://luthien.cc/install.sh` installer, `/policy-config`, `/history`
(`.session-card` rows), `/api/admin/policy/set`, `/api/admin/credentials`,
and the `MockAnthropicServer` API.  Not yet re-verified by an actual render:

- **Auth settings are DB-backed now.** `mock_runtime.py` writes
  `AUTH_MODE=passthrough` to `.env`, but that only seeds the default on a
  gateway's first boot; an existing install keeps the auth mode and
  credential-validation setting saved in its database (managed via
  `/api/admin/auth/config`).  The old `VALIDATE_CREDENTIALS` env var no longer
  exists and was dropped.  If takes fail with auth errors, set passthrough and
  disable validation through that endpoint or the admin UI.
- **Pacing and selectors.** Tape `Sleep`s and the Playwright click targets
  (`BlockDangerousCommandsPolicy` card on `/policy-config`) were tuned against
  the May 2026 UI and CLI output; re-check after rendering.
- **No clips rendered from this branch yet.** Treat the first render as the
  test.
