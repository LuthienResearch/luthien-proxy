"""Unit tests for Jinja2 fragment templates."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent.parent / "src" / "luthien_proxy" / "templates"


@pytest.fixture
def env():
    """Jinja2 environment for testing."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _make_session(**kwargs) -> dict:
    base = {
        "session_id": "test-123",
        "first_ts": "2025-01-01T10:00:00",
        "last_ts": "2025-01-01T11:00:00",
        "last_ts_formatted": "1y ago",
        "preview": "hello",
        "turn_count": 3,
        "models_used": ["claude-3"],
        "policy_interventions": 0,
    }
    base.update(kwargs)
    return base


def test_sessions_template_renders(env):
    tpl = env.get_template("fragments/sessions.html")
    out = tpl.render(sessions=[_make_session()], next_cursor="abc123")
    assert "test-123" in out
    assert 'data-cursor="abc123"' in out
    assert "load-more-sentinel" in out
    assert "session-card" in out
    assert "3 turns" in out
    assert "claude-3" in out


def test_sessions_template_xss_safe(env):
    tpl = env.get_template("fragments/sessions.html")
    out = tpl.render(
        sessions=[_make_session(session_id="<script>alert(1)</script>", preview="")],
        next_cursor=None,
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_sessions_template_no_sentinel_when_no_cursor(env):
    """Test that sessions template omits sentinel when no next_cursor."""
    tpl = env.get_template("fragments/sessions.html")
    out = tpl.render(sessions=[], next_cursor=None)
    assert "load-more-sentinel" not in out


def test_turns_template_renders(env):
    """Test that turns template renders with data."""
    tpl = env.get_template("fragments/turns.html")
    out = tpl.render(
        turns=[
            {
                "event_id": "evt-456",
                "created_at": "2025-01-01",
                "event_type": "request",
                "payload_preview": "test",
            }
        ],
        next_cursor="xyz789",
    )
    assert "evt-456" in out
    assert 'data-last-event-id="evt-456"' in out
    assert 'data-cursor="xyz789"' in out


def test_turns_template_xss_safe(env):
    """Test that turns template escapes user content."""
    tpl = env.get_template("fragments/turns.html")
    out = tpl.render(
        turns=[
            {
                "event_id": "<img src=x onerror=alert(1)>",
                "created_at": "",
                "event_type": "response",
                "payload_preview": "",
            }
        ],
        next_cursor=None,
    )
    assert "<img" not in out
    assert "&lt;img" in out


def test_turns_template_no_sentinel_when_no_cursor(env):
    """Test that turns template omits sentinel when no next_cursor."""
    tpl = env.get_template("fragments/turns.html")
    out = tpl.render(turns=[], next_cursor=None)
    assert "load-more-sentinel" not in out


def test_sessions_template_autoescape_enabled(env):
    """Test that autoescape block is present in sessions template."""
    with open(TEMPLATES_DIR / "fragments" / "sessions.html") as f:
        content = f.read()
    assert "{% autoescape true %}" in content
    assert "{% endautoescape %}" in content


def test_turns_template_autoescape_enabled(env):
    """Test that autoescape block is present in turns template."""
    with open(TEMPLATES_DIR / "fragments" / "turns.html") as f:
        content = f.read()
    assert "{% autoescape true %}" in content
    assert "{% endautoescape %}" in content
