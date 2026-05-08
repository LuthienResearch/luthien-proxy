"""Headless browser recording of the policy-config UI for demo videos.

Uses Playwright's built-in video recorder.  No macOS Screen Recording
permission needed — Playwright captures its own browser frames.

Run from the repo root:

    uv run --with playwright python assets/demos/record_policy_config.py

Output:    assets/demos/raw/policy-config.webm  (then converted to mp4)

The script:
  1. Launches a headed Chromium at 1600x1000.
  2. Navigates to /policy-config on the local gateway.
  3. Pauses long enough for visual layout, scrolls the policy list, clicks
     into a policy, activates it, scrolls back.  Pacing is deliberate — these
     dwell times are what the viewer sees.
  4. Closes the browser; Playwright finalizes the video.
  5. ffmpeg-converts webm → mp4 to match the rest of the demo set.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "assets" / "demos" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

GATEWAY_URL = "http://localhost:8001"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=str(RAW),
            record_video_size={"width": 1600, "height": 1000},
        )
        page = context.new_page()

        # Beat 0: navigate, hold for the page to settle.
        page.goto(f"{GATEWAY_URL}/policy-config", wait_until="networkidle")
        page.wait_for_timeout(2000)

        # Beat 1: scroll the policy list to show what's available.
        page.mouse.move(800, 500)
        for _ in range(4):
            page.mouse.wheel(0, 200)
            page.wait_for_timeout(400)
        page.wait_for_timeout(800)

        # Beat 2: scroll back to the top.
        page.mouse.wheel(0, -1200)
        page.wait_for_timeout(1500)

        # Beat 3: click a non-active policy card.  Try a sensible target
        # first; fall back to text search.
        try:
            page.get_by_text("BlockDangerousCommandsPolicy").first.click(timeout=3000)
        except Exception:
            page.get_by_text("StringReplacementPolicy").first.click(timeout=3000)
        page.wait_for_timeout(2500)

        # Beat 4: hold the detail view for a beat so the viewer can read it.
        page.wait_for_timeout(2000)

        context.close()
        browser.close()

    # Playwright names videos by random hex; rename the most recent one.
    webms = sorted(RAW.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webms:
        print("No video produced.", file=sys.stderr)
        sys.exit(1)
    src = webms[0]
    dst_webm = RAW / "policy-config.webm"
    if src != dst_webm:
        shutil.move(str(src), str(dst_webm))
    print(f"webm: {dst_webm}")

    dst_mp4 = RAW / "policy-config.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(dst_webm),
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(dst_mp4),
        ],
        check=True,
        capture_output=True,
    )
    print(f"mp4: {dst_mp4}")


if __name__ == "__main__":
    main()
