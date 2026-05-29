---
category: Fixes
---

**Fix stored-XSS class in admin UI static assets**: A comprehensive sweep of
`src/luthien_proxy/static/**`. Attacker-influenced values (session_id, call_id,
transaction_id, tool_call_id, key_hash, credential/model/endpoint/provider
names, full activity-stream event payloads, server error messages) were
interpolated into inline `onclick` JS strings, quoted HTML attributes, or HTML
text — some via hand-rolled escapers that did not escape `'`/`"`, some (in
`credentials.html`) via raw string concatenation with no escaping at all, some
(in `activity_monitor.js`) via `JSON.stringify(event)` straight into a `<pre>` —
allowing breakout and script execution.
  - Migrated the attacker-controlled JS-string / event-handler / HTML-text sinks
    in `history_list.html`, `diff_viewer.html`, `request_logs.html`,
    `inference_providers.html`, `credentials.html`, `config_dashboard.html`, and
    `activity_monitor.js` to DOM construction (`createElement` / `textContent` /
    `addEventListener` / `dataset`) — markup and quotes are inert by
    construction, no new dependency.
  - Hardened the retained escapers (`escapeHtml` in `conversation_live.js` /
    `diff_viewer.html`, `esc` in `config_dashboard.html`) to escape all five
    HTML-significant characters so attribute interpolation cannot break out.
  - Converted `e.message` → `innerHTML` error sinks (unconstrained server text)
    to `textContent` across the affected files.
  - Added source-level regression guards in
    `tests/luthien_proxy/unit_tests/ui/test_static_xss_guards.py`.
