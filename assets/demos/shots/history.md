# Shot list: conversation history UI

Browser screencap of `http://localhost:8000/history` and a single conversation
detail view.  This is the "Luthien logs everything" beat.

**Output:** `assets/demos/raw/history.mp4`

## Capture

Same as `policy-config.md` — QuickTime selection recording → ffmpeg to MP4.

## Browser setup

Same as `policy-config.md`: Chrome incognito, ~1600x1000, no DevTools.

## Pre-conditions

- Gateway running with at least 2-3 recent conversations recorded.  Fastest
  way to seed: run `policy-in-action.tape` first, then a couple of plain
  `luthien claude -p "what is 2+2"` calls.
- A conversation that includes a policy decision is the money shot — make
  sure one exists from a `BlockDangerousCommandsPolicy` or `PreferUvPolicy`
  run.

## Beats

1. **0:00–0:04** — Page load on `/history`.  Show the list of recorded
   conversations with timestamps and policy info.
2. **0:04–0:08** — Click into the most recent policy-blocked conversation.
3. **0:08–0:14** — Scroll through the request/response detail.  Highlight the
   policy decision in the timeline.
4. **0:14–0:20** — Hit **Live view** if available, or navigate back and
   click into a different conversation to show consistency.

Total target: **~20s raw**, trim to 10–12s in editing.

## Optional: live streaming clip

For a dynamic shot, open `/conversation/live/<id>` in one tab while running
`luthien claude -p "..."` in a terminal.  Record the browser tab as events
stream in real time.  This belongs in its own clip — `raw/history-live.mp4`.
