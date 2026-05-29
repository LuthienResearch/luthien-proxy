"""Regression guards against the stored-XSS class in admin UI static assets.

Background: the admin UI is vanilla JS served as static HTML. Several render
paths used to build HTML by string interpolation and rely on a hand-rolled
`escapeHtml` that escaped ``<``, ``>``, ``&`` but NOT ``'`` or ``"``. Any
attacker-influenced value (session_id, call_id, transaction_id, tool_call_id,
model/endpoint names — all request- or model-derived) interpolated into an
inline ``onclick="...('${x}')"`` JS string or a quoted HTML attribute could
break out and execute. See PR addressing this class.

The fix:
  * Genuinely attacker-controlled JS-string / event-handler sinks were rewritten
    using DOM construction (``createElement`` / ``textContent`` /
    ``addEventListener`` / ``dataset``) so quotes and markup are inert by
    construction — the browser-native, zero-dependency safe answer.
  * The remaining hand-rolled ``escapeHtml`` helpers were hardened to also
    escape quotes so attribute interpolation cannot break out.

These tests are deliberately source-text assertions (the repo has no JS test
runtime). They are not a substitute for a real DOM test, but they pin the two
concrete regressions that already bit this codebase twice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[4] / "src" / "luthien_proxy" / "static"

# Files whose attacker-influenced render paths were migrated to DOM construction.
DOM_MIGRATED_FILES = [
    "history_list.html",
    "diff_viewer.html",
    "request_logs.html",
    "inference_providers.html",
]

# Files that retain a hand-rolled escapeHtml used in attribute contexts; those
# escapers must cover quotes.
QUOTE_SAFE_ESCAPER_FILES = [
    "conversation_live.js",
    "diff_viewer.html",
]

# Matches an inline event-handler attribute (onclick=, onchange=, ...) whose
# value contains a JS string literal built from a template-literal placeholder,
# e.g.  onclick="viewSession('${escapeHtml(session.session_id)}')"
# This is the exact stored-XSS pattern we removed; escapeHtml does not escape
# the surrounding single quotes, so the placeholder can break out of the JS
# string. New occurrences should use addEventListener instead.
_INLINE_HANDLER_JS_STRING = re.compile(
    r"""on\w+\s*=\s*["'][^"']*\(\s*\\?['"]\$\{""",
    re.IGNORECASE,
)


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", DOM_MIGRATED_FILES)
def test_no_inline_handler_js_string_interpolation(filename: str) -> None:
    """No inline on*-handler should interpolate a template placeholder into a JS string.

    That pattern is unsafe with any HTML-only escaper. Event wiring must go
    through addEventListener so the value is passed as data, never parsed.
    """
    source = _read(filename)
    matches = _INLINE_HANDLER_JS_STRING.findall(source)
    assert not matches, (
        f"{filename} contains inline-handler JS-string interpolation (stored-XSS class): "
        f"{matches}. Use addEventListener + dataset instead."
    )


@pytest.mark.parametrize("filename", QUOTE_SAFE_ESCAPER_FILES)
def test_escapehtml_escapes_quotes(filename: str) -> None:
    """Any retained hand-rolled escapeHtml must escape both single and double quotes.

    The old textContent/innerHTML trick left quotes unescaped, so a value placed
    in a quoted attribute could break out. Assert the source escapes them.
    """
    source = _read(filename)
    assert "escapeHtml" in source, f"{filename} no longer defines escapeHtml; update this guard."
    assert "&quot;" in source, f"{filename}'s escapeHtml must escape double quotes (&quot;)."
    assert "&#39;" in source or "&apos;" in source, f"{filename}'s escapeHtml must escape single quotes (&#39;)."


def test_history_list_uses_dom_construction_for_session_id() -> None:
    """The session_id sink in history_list.html must be wired via addEventListener.

    This is the #780 / pre-existing history_list sink. It must not appear in an
    inline onclick JS string anymore.
    """
    source = _read("history_list.html")
    assert "viewSession('${" not in source
    assert "addEventListener('click', () => viewSession(" in source


def test_request_logs_uses_dom_construction_for_transaction_id() -> None:
    """transaction_id must be wired via addEventListener, not an inline onclick string."""
    source = _read("request_logs.html")
    assert "showTransaction('${" not in source
    assert "addEventListener('click', () => showTransaction(" in source


def test_diff_viewer_uses_dom_construction_for_call_id() -> None:
    """call_id must be wired via addEventListener, not an inline onclick string."""
    source = _read("diff_viewer.html")
    assert "selectCall('${" not in source
    assert "addEventListener('click', () => selectCall(" in source


def test_inference_providers_uses_dom_construction_for_provider_name() -> None:
    """Provider-name edit/delete buttons must be wired via addEventListener."""
    source = _read("inference_providers.html")
    assert "editProvider(\\'" not in source
    assert "deleteProvider(\\'" not in source
    assert "addEventListener('click', () => editProvider(" in source
    assert "addEventListener('click', () => deleteProvider(" in source
