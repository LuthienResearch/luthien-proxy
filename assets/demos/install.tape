# install.tape — fresh `curl|bash` install + `luthien onboard`.
#
# Render:    vhs assets/demos/install.tape
# Output:    assets/demos/raw/install.mp4
#
# Pre-conditions:
#   - Recording machine should NOT have luthien-cli installed.
#     Reset:  uv tool uninstall luthien-cli && rm -rf ~/.luthien
#   - Network access to luthien.cc and PyPI.
#   - Claude Code CLI installed and OAuth-authed.  (We exit before launching
#     it, so this is only required if you want to extend the tape.)
#
# Notes:
#   - Real install ~15-25s.  Edit pacing in post if needed; do not jam Sleeps.
#   - The `Continue? [Y/n]` prompt is answered with `y`.
#   - Final beat: hold the "Ready" panel for ~5s so editors can grab a still.

Output "assets/demos/raw/install.mp4"

Set FontSize 28
Set Width 1920
Set Height 1080
Set Theme "Catppuccin Mocha"
Set TypingSpeed 70ms
Set PlaybackSpeed 1.0
Set Padding 40

Hide
Type "clear"
Enter
Show
Sleep 800ms

Type "curl -fsSL https://luthien.cc/install.sh | bash"
Sleep 700ms
Enter

# uv install + luthien-cli install + onboard banner
Sleep 18s

# Continue prompt
Type "y"
Enter

# proxy install + gateway boot + Ready panel + "press any key" prompt
Sleep 15s

# Quit before claude launches — keeps this clip terminal-only and reusable
Type "q"
Sleep 8s
