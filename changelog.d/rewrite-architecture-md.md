---
category: Chores & Docs
---

**Rewrite ARCHITECTURE.md to match the current codebase**: The doc referenced several phantom modules (`llm/litellm_client.py`, `pipeline/processor.py`, `policy_core/openai_interface.py`, `policy_core/streaming_policy_context.py`, a `streaming/` package) and omitted modules that actually exist (`credential_manager`, `policy_manager`, `usage_telemetry/`, `request_log/`, `history/` as a top-level module). The UI route list and data model also drifted. Full rewrite verified against source, migrations, and route decorators.
