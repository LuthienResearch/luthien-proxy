"""Regression guards against the stored-XSS class in admin UI static assets.

Background: the admin UI is vanilla JS served as static HTML. Several render
paths built HTML by string interpolation — some via a hand-rolled `escapeHtml`
that escaped ``<``, ``>``, ``&`` but NOT ``'`` or ``"``, some (credentials.html)
via raw concatenation with no escaper at all. Any attacker-influenced value
(session_id, call_id, transaction_id, tool_call_id, key_hash, credential/model/
endpoint/provider names, server error messages — all request-, model-, or
server-derived) interpolated into an inline ``onclick`` JS string or a quoted
HTML attribute could break out and execute.

The fix:
  * Genuinely attacker-controlled JS-string / event-handler sinks were rewritten
    using DOM construction (``createElement`` / ``textContent`` /
    ``addEventListener`` / ``dataset``) so quotes and markup are inert by
    construction — the browser-native, zero-dependency safe answer.
  * The remaining hand-rolled ``escapeHtml`` helpers were hardened to also
    escape quotes so attribute interpolation cannot break out.

These tests are deliberately source-text assertions (the repo has no JS test
runtime). They are not a substitute for a real DOM test, but they pin the
concrete regressions that already bit this codebase repeatedly.
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
    "credentials.html",
]

# Files that retain a hand-rolled escapeHtml used in attribute contexts; those
# escapers must cover quotes.
QUOTE_SAFE_ESCAPER_FILES = [
    "conversation_live.js",
    "diff_viewer.html",
]

# Matches an inline event-handler attribute (onclick=, onchange=, ...) whose
# value opens a JS string literal that is then filled with a dynamic value —
# the exact stored-XSS class we removed, because the surrounding JS quotes are
# not escaped by any HTML escaper, so the value can break out of the string.
#
# Two concrete dynamic shapes are caught after the opening JS quote (' or "):
#   * template-literal interpolation:  onclick="f('${x}')"
#   * string concatenation:            onclick="f('" + x + "')"
#                                      onclick='f(\'' + x + '\')'
# Both forms appeared in this codebase (the latter in credentials.html, which
# had no escapeHtml at all, so an escapeHtml-name grep would not surface it).
#
# Coverage caveats (deliberately documented rather than over-claimed):
#   * `[^)]*?` skips any *preceding* literal args up to the first dynamic JS
#     quote, so multi-arg shapes like onclick="f(1, '${x}')" are caught — but
#     only the FIRST dynamic string in the handler. A handler whose first arg is
#     a safe constant string and whose SECOND arg is dynamic via a different
#     delimiter could slip past; in practice handlers in this repo take the
#     dynamic id first, so this is an accepted gap, not full coverage.
#   * It detects the *opening* unsafe construction; it does not parse JS, so it
#     cannot prove the value is attacker-influenced — it flags the dangerous
#     idiom regardless and expects addEventListener instead.
_INLINE_HANDLER_JS_STRING = re.compile(
    r"""on\w+\s*=\s*["']"""  # event-handler attribute opens (HTML quote)
    r"""[^)]*?\("""  # up to and including the call's opening paren
    r"""[^)]*?"""  # any preceding literal args
    r"""(?:\\?['"])"""  # the opening quote of a JS string arg (maybe \-escaped)
    r"""(?:\$\{|"\s*\+|'\s*\+|\\?['"]\s*\+)""",  # ${...} OR a concat seam ("+ / '+)
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


def test_credentials_uses_dom_construction_for_key_hash() -> None:
    """key_hash must be wired via addEventListener + dataset, not concat onclick.

    credentials.html built HTML by raw string concatenation with no escaper at
    all — onclick="invalidateOne('" + cred.key_hash + "')" plus key_hash in a
    title="" attribute. Both must be gone.
    """
    source = _read("credentials.html")
    assert "invalidateOne(\\'" not in source
    assert "onclick=" not in source, "credentials.html must not use inline onclick handlers."
    assert "addEventListener('click', () => invalidateOne(" in source


def test_inline_handler_guard_catches_concat_shape() -> None:
    """The widened guard must catch the string-concat onclick shape, not just ${...}.

    Self-test: the credentials.html-style concat sink and the multi-arg
    template-literal shape must both match; a delegated/static handler must not.
    """
    assert _INLINE_HANDLER_JS_STRING.search('onclick="invalidateOne(\'" + cred.key_hash + "\')"')
    assert _INLINE_HANDLER_JS_STRING.search("onclick=\"foo(1, '${x}')\"")
    assert not _INLINE_HANDLER_JS_STRING.search('onclick="closeDetail()"')
    assert not _INLINE_HANDLER_JS_STRING.search('data-action="srv-delete" data-name="x"')
