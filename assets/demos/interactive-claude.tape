# interactive-claude.tape — full Claude Code TUI through luthien proxy.
#
# Render:    vhs assets/demos/interactive-claude.tape
# Output:    assets/demos/raw/interactive-claude.mp4
#
# Pre-conditions:
#   - `mock_runtime.py` running:
#         uv run python assets/demos/mock_runtime.py
#   - Claude Code authed, terms accepted, theme set (i.e. NOT first launch).
#   - Re-seed mock between takes:
#         kill -USR1 $(pgrep -f mock_runtime.py)
#
# Notes:
#   - Unlike `claude -p`, this drives the actual Ink TUI.  VHS provides a real
#     pty so kqueue input polling works.
#   - Mock returns a deterministic response, so the stream completes in <2s
#     regardless of network.  If the response feels rushed, raise the post-
#     Enter Sleep below.

Output "assets/demos/raw/interactive-claude.mp4"

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
Sleep 1500ms

Type "luthien claude"
Enter

# Splash + Ready prompt to settle.
Sleep 7s

Type "How do I install the requests library? One shell command."
Sleep 600ms
Enter

# Response stream + dwell for reading.
Sleep 14s

# Exit cleanly so the recording's tail isn't mid-TUI.
Type "/exit"
Enter
Sleep 3s
