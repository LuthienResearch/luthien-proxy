# policy-in-action.tape — CLI tour of policy management.  Shows the operator
# listing policies, inspecting the active one, switching it, and confirming.
# No Claude API calls — this clip is about the control surface, not the
# behavioral effect.  Pair with with-luthien-prefer-uv.mp4 in editing if you
# want a "configure → see the effect" sequence.
#
# Render:    vhs assets/demos/policy-in-action.tape
# Output:    assets/demos/raw/policy-in-action.mp4
#
# Pre-conditions:
#   - `luthien onboard` already run; gateway healthy.
#   - Active policy can be anything to start; the tape switches it during
#     the recording.

Output "assets/demos/raw/policy-in-action.mp4"

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

# Beat 1 — what's available
Type "luthien policy list"
Enter
Sleep 7s

# Beat 2 — switch
Type "luthien policy set NoOpPolicy"
Enter
Sleep 4s

# Beat 3 — confirm
Type "luthien policy current"
Enter
Sleep 6s
