# without-luthien.tape — Claude Code answering directly, no proxy.
#
# This is the "before" half of the with/without contrast.  Same prompt as
# with-luthien-prefer-uv.tape; the only difference is that here Claude talks
# straight to the Anthropic API and recommends pip.
#
# Render:    vhs assets/demos/without-luthien.tape
# Output:    assets/demos/raw/without-luthien.mp4
#
# Pre-conditions:
#   - `mock_runtime.py` running in another terminal:
#         uv run python assets/demos/mock_runtime.py
#   - Claude Code installed (no auth needed — mock doesn't validate).
#   - The tape sets `ANTHROPIC_BASE_URL=http://localhost:18888` (the mock) so
#     `claude -p` talks straight to mock, NOT through the gateway.  This is
#     the "no luthien" half of the with/without contrast.
#   - Re-seed the mock queue between takes:
#         kill -USR1 $(pgrep -f mock_runtime.py)
#
# Notes:
#   - Mock returns the FIRST queued response: text "`pip install requests`".
#   - Real Anthropic was non-deterministic about formatting (sometimes
#     fenced, sometimes inline), which broke the side-by-side contrast.
#     Mock pins the format so both halves cut together cleanly.

Output "assets/demos/raw/without-luthien.mp4"

Set FontSize 28
Set Width 1920
Set Height 1080
Set Theme "Catppuccin Mocha"
Set TypingSpeed 70ms
Set Padding 40

Hide
Type "export ANTHROPIC_BASE_URL=http://localhost:18888"
Enter
Type "export ANTHROPIC_API_KEY=mock-key"
Enter
Type "clear"
Enter
Show
Sleep 1500ms

Type `claude -p "How do I install the requests library? Give me one shell command."`
Enter

# Wait for the streamed response to complete + a beat for editing
Sleep 16s
