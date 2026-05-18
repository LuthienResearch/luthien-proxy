---
category: Features
pr: 700
---

**StringReplacementPolicy request-side filtering**: Added an `apply_to: "request" | "response" | "both"` field to `StringReplacementConfig` (default `"response"`, preserves back-compat). When `apply_to` includes `"request"`, the policy now scrubs incoming user content — string message content, `text` blocks, and `tool_result` content (string and list-of-text-blocks forms) — before forwarding to the backend. `tool_use`, `image`, `thinking`, and the top-level `system` field are not touched. Emits `policy.string_replacement.request_modified` once per request that had any substitutions, mirroring the response-side event payload shape from #693. The hook deep-copies messages before mutation so `original_request` recorded in transaction history remains the user's verbatim input. Closes #557.
