# Shot list: policy-config UI

Browser screencap of `http://localhost:8000/policy-config`.

**Output:** `assets/demos/raw/policy-config.mp4`

## Capture

Use **macOS QuickTime → File → New Screen Recording** with **Selection** mode.
Drag the selection rectangle around the browser window only — exclude desktop,
dock, menu bar.  QuickTime saves a `.mov`; convert to MP4 with:

```bash
ffmpeg -i raw/policy-config.mov -c:v libx264 -crf 20 -preset slow -movflags +faststart -an raw/policy-config.mp4
```

## Browser setup

- Chrome in **Incognito** mode (no extension chrome, no history clutter).
- Window size: ~1600x1000.  Fits the page without horizontal scroll, leaves
  some breathing room around the content.
- Zoom: ⌘+ once or twice if text feels small in the recording.
- DevTools closed.

## Pre-conditions

- `luthien onboard` complete; gateway running on http://localhost:8000.
- A few policies should be visible besides the active one.  Default policy
  catalog is fine.

## Beats

1. **0:00–0:03** — Page load.  Cursor parked off-screen-right.  Active policy card
   visible at top.
2. **0:03–0:08** — Slow scroll down to show the full policy list.  Pause at the
   bottom for a second.  Scroll back up.
3. **0:08–0:13** — Click into a non-active policy card (e.g. `BlockDangerousCommandsPolicy`).
   Show its config schema and description.
4. **0:13–0:18** — Click the **Activate** / **Set as active** action.  Confirm dialog
   if any.  Wait for the success toast / refresh.
5. **0:18–0:22** — Scroll back to top.  The newly-active policy is now pinned at
   the top.  Hold this frame for ~3s.

Total target: **~22s raw**, trim down to 10–15s in editing.

## Tips

- Move the cursor deliberately, slowly.  Fast cursor jumps look frantic in 30fps
  playback and are hard to follow.
- Avoid double-clicking — single clicks read more clearly on video.
- If the page has a login screen on first load (it shouldn't on localhost with
  `LOCALHOST_AUTH_BYPASS=true`), check `.env` and restart the gateway.
