Objective: Capture the standard local workflow steps for bug bash so we follow the same startup/test/launch sequence.

Acceptance:
- Objective includes the exact command sequence for starting the stack, testing, and launching Claude Code or Codex.

Workflow steps:
```bash
cd /Users/scottwofford/build/luthien-proxy
git checkout main && git pull
"Start Docker"
./scripts/quick_start.sh
./scripts/test_gateway.sh
./scripts/launch_claude_code.sh
```
OR
```bash
./scripts/launch_codex.sh
```

Notes:
4) Untracked file: dev/debug_data/conversation_log_template.csv
