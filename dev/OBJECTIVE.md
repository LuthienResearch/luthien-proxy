# Objective

Add a long-term stress testing script (`scripts/long_term_test.sh`) that makes repeated Claude API calls through the proxy over extended periods to validate stability.

## Acceptance Criteria

- Script accepts CLI args for max time, max calls, prompt, port, cooldown, output dir, and no-start-proxy flag
- Optionally starts/stops the proxy via docker compose
- Runs Claude Code in a loop, resuming the same session, logging results
- Handles signals gracefully and prints a summary on exit
