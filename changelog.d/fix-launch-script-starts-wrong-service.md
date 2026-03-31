---
category: Fixes
pr: 452
---

**launch_claude_code.sh starts wrong service**: The script called `observability.sh up -d` instead of `start_gateway.sh` when the gateway health check failed, so the gateway never actually started.
