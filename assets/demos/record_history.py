"""Headless browser recording of the conversation history UI.

See record_policy_config.py for the rationale (Playwright > screencap +
permissions for browser-only demos).

Run:    uv run --with playwright python assets/demos/record_history.py
Output: assets/demos/raw/history.{webm,mp4}
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

        page.goto(f"{GATEWAY_URL}/history", wait_until="networkidle")
        page.wait_for_timeout(2500)

        # Scroll the conversation list to show volume of recorded traffic.
        page.mouse.move(800, 500)
        for _ in range(3):
            page.mouse.wheel(0, 250)
            page.wait_for_timeout(500)
        page.wait_for_timeout(700)
        page.mouse.wheel(0, -750)
        page.wait_for_timeout(1500)

        # Click into the most-recent conversation.  Rows are `.session-card`
        # divs with `onclick="viewSession(...)"` that navigates to
        # /conversation/live/<id>.
        page.locator(".session-card").first.click(timeout=5000)
        # Conversation detail keeps an SSE stream open, so "networkidle"
        # never fires.  Wait for domcontentloaded + a fixed dwell instead.
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)

        # Scroll through the detail view so the viewer sees request/response
        # blocks and any policy intervention markers.
        for _ in range(3):
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(1200)
        page.wait_for_timeout(2000)

        context.close()
        browser.close()

    webms = sorted(RAW.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not webms:
        print("No video produced.", file=sys.stderr)
        sys.exit(1)
    src = webms[0]
    dst_webm = RAW / "history.webm"
    if src != dst_webm:
        shutil.move(str(src), str(dst_webm))
    print(f"webm: {dst_webm}")

    dst_mp4 = RAW / "history.mp4"
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
