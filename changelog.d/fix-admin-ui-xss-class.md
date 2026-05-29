---
category: Fixes
---

**Fix stored-XSS class in admin UI static assets**: Attacker-influenced values
(session_id, call_id, transaction_id, tool_call_id, model/endpoint, provider
names) were interpolated into inline `onclick="...('${x}')"` JS strings and
quoted HTML attributes, while the hand-rolled `escapeHtml` helpers did not
escape `'` or `"`, allowing breakout and script execution.
  - Migrated the genuinely attacker-controlled JS-string/event-handler sinks in
    `history_list.html`, `diff_viewer.html`, `request_logs.html`, and
    `inference_providers.html` to DOM construction (`createElement` /
    `textContent` / `addEventListener` / `dataset`) — markup and quotes are
    inert by construction, no new dependency.
  - Hardened the retained `escapeHtml` helpers in `conversation_live.js` and
    `diff_viewer.html` to escape all five HTML-significant characters so
    attribute interpolation cannot break out.
  - Added source-level regression guards in
    `tests/luthien_proxy/unit_tests/ui/test_static_xss_guards.py`.
