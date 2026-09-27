# with-luthien-prefer-uv.tape — same prompt as without-luthien.tape, but
# routed through the proxy with a pip→uv judge policy active.  The judge
# rewrites any `pip install` reference into the `uv` equivalent.
#
# Render:    vhs assets/demos/with-luthien-prefer-uv.tape
# Output:    assets/demos/raw/with-luthien-prefer-uv.mp4
#
# Pre-conditions:
#   - `mock_runtime.py` running in another terminal:
#         uv run python assets/demos/mock_runtime.py
#     That handles all the setup:  mock backend on 18888, gateway pointed at
#     it, SimpleLLMPolicy with pip→uv judge active, response queue seeded.
#   - Claude Code installed (no auth needed — mock doesn't validate).
#   - Re-seed the queue between takes:
#         kill -USR1 $(pgrep -f mock_runtime.py)
#
# Notes:
#   - We pair this clip with without-luthien.mp4 in editing.  Same width,
#     font, theme — they cut together cleanly.

Output "assets/demos/raw/with-luthien-prefer-uv.mp4"

Set FontSize 28
Set Width 1920
Set Height 1080
Set Theme "Catppuccin Mocha"
Set TypingSpeed 70ms
Set Padding 40

Hide
Type "clear"
Enter
Show
Sleep 800ms

Type `luthien claude -p "How do I install the requests library? Give me one shell command."`
Enter

# Claude latency + judge latency (Haiku) ≈ 4-9s.  Pad for editing.
Sleep 18s
