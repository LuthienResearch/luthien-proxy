# Demo recordings

Source material for the demo videos in the project README.  Raw MP4 clips
land in `raw/` (gitignored), get edited together externally, and the finished
cuts get committed to `assets/readme/`.

## Layout

```
assets/demos/
  README.md                              # this file
  mock_runtime.py                        # mock backend + reconfigured gateway
  install.tape                           # VHS — install + onboard
  without-luthien.tape                   # VHS — claude direct, no proxy
  with-luthien-prefer-uv.tape            # VHS — same prompt, judge rewrites pip→uv
  policy-in-action.tape                  # VHS — CLI tour: list / set / current
  shots/
    policy-config.md                     # browser screencap storyboard
    history.md                           # browser screencap storyboard
  raw/                                   # gitignored — generated clips
  regen.sh                               # render-all helper
```

## Tooling

```bash
brew install vhs ffmpeg
```

## Recording flow

1. **Boot the mock runtime in one terminal:**

   ```bash
   uv run python assets/demos/mock_runtime.py
   ```

   This:
   - starts a `MockAnthropicServer` on port 18888,
   - rewrites `~/.luthien/luthien-proxy/.env` to point upstream at the mock,
   - restarts the gateway via `luthien down && luthien up`,
   - stores a fake `anthropic` server credential,
   - activates `SimpleLLMPolicy` with a pip→uv judge config (api_base = mock),
   - pre-enqueues responses so the next user-prompt + judge-call pair return
     deterministic output.

   Leave it running.  Ctrl-C tears down: restores the original `.env`, restarts
   the gateway, stops the mock.

2. **Render tapes against mock in a second terminal:**

   ```bash
   vhs assets/demos/with-luthien-prefer-uv.tape
   ```

   Re-seed the response queue between takes:

   ```bash
   kill -USR1 $(pgrep -f mock_runtime.py)
   ```

3. **Recording a tape that needs real network** (`install.tape`): stop the
   runtime first.  The install tape exercises a fresh `curl|bash` install,
   not gateway behavior.

## Tapes (terminal recordings)

| Tape | Output | Mock runtime? | Pre-conditions |
|---|---|---|---|
| `install.tape` | `raw/install.mp4` | No | luthien-cli NOT installed; reset with `uv tool uninstall luthien-cli && rm -rf ~/.luthien` |
| `without-luthien.tape` | `raw/without-luthien.mp4` | Optional | Claude Code authed; tape unsets `ANTHROPIC_BASE_URL` |
| `with-luthien-prefer-uv.tape` | `raw/with-luthien-prefer-uv.mp4` | **Yes** | mock_runtime.py running with seeded queue |
| `policy-in-action.tape` | `raw/policy-in-action.mp4` | No (gateway must be running) | any active policy |

## With/without contrast

`without-luthien.tape` and `with-luthien-prefer-uv.tape` use the **identical
prompt**: "How do I install the requests library? Give me one shell command."
Without the proxy: ``pip install requests``.  With the proxy + pip→uv judge:
``uv pip install requests``.  Same theme, font, width — they cut together
side-by-side cleanly.

Side-by-side recipe:

```bash
ffmpeg -i raw/without-luthien.mp4 -i raw/with-luthien-prefer-uv.mp4 \
       -filter_complex hstack -c:v libx264 -crf 20 -preset slow \
       -movflags +faststart -an raw/contrast-side-by-side.mp4
```

## Why mock the backend

- **Free**: no real Anthropic API calls.  Iterate as much as you want.
- **Deterministic**: identical pixel output every render.  No "Claude said it
  differently this time" surprises.
- **Avoids the OAuth-judge bug** ([flagged separately]): on a fresh
  OAuth-only `luthien onboard`, every preset policy fails because LiteLLM
  sends OAuth tokens as `x-api-key`.  The mock backend doesn't auth-check, and
  the mock-judge config uses a stored `server_key` credential anyway.

The mock infra was already in the repo for tests
(`tests/luthien_proxy/e2e_tests/mock_anthropic/`).  `mock_runtime.py` just
wires it into a demo-friendly orchestration: pre-seeded responses, gateway
restart, policy activation, atomic teardown.

## Browser captures (manual)

VHS doesn't record browsers.  For `/policy-config` and `/history` UI clips,
use macOS QuickTime → New Screen Recording → Selection.  Storyboards in
`shots/`.  Convert `.mov` → MP4:

```bash
ffmpeg -i raw/<name>.mov -c:v libx264 -crf 20 -preset slow \
       -movflags +faststart -an raw/<name>.mp4
```

## Editing

Drop raw MP4s into your editor.  Tapes are intentionally one-beat-each so
reordering, trimming, and side-by-sides are cheap.  When a final cut lands,
export to `assets/readme/<name>.mp4` (commit) and update the project
`README.md` to embed it.

## Why VHS, not asciinema or screencap

VHS scripts are reproducible: when CLI output changes, re-run the same tape
and the clip updates.  Asciinema's `.cast` is a one-shot tied to a session.
Real screencap is still needed for the browser UI.
