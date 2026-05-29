---
category: Fixes
---

**Fix stored-XSS class in admin UI static assets**: Attacker-influenced values
(session_id, call_id, transaction_id, tool_call_id, key_hash, credential/model/
endpoint/provider names, server error messages) were interpolated into inline
`onclick` JS strings and quoted HTML attributes — some via hand-rolled
`escapeHtml` helpers that did not escape `'` or `"`, some (in `credentials.html`)
via raw string concatenation with no escaping at all — allowing breakout and
script execution.
  - Migrated the genuinely attacker-controlled JS-string/event-handler sinks in
    `history_list.html`, `diff_viewer.html`, `request_logs.html`,
    `inference_providers.html`, and `credentials.html` to DOM construction
    (`createElement` / `textContent` / `addEventListener` / `dataset`) — markup
    and quotes are inert by construction, no new dependency.
  - Hardened the retained `escapeHtml` helpers in `conversation_live.js` and
    `diff_viewer.html` to escape all five HTML-significant characters so
    attribute interpolation cannot break out.
  - Added source-level regression guards in
    `tests/luthien_proxy/unit_tests/ui/test_static_xss_guards.py`.
