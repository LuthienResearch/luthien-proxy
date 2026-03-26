---
category: Features
---

**Streaming protocol compliance validator**: Added a pipeline-level validator that checks Anthropic streaming event ordering after each stream completes. Logs warnings on violations (content blocks after message_delta, unclosed blocks, etc.) and records them as policy events and OTel span attributes. This is the architectural prevention for the class of streaming ordering bugs seen in PRs #134 and #356.
