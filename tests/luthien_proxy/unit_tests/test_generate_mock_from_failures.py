"""Unit tests for scripts/generate_mock_from_failures.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import generate_mock_from_failures as mod
from generate_mock_from_failures import _render_test, _sanitize_name, generate, load_entries

# =============================================================================
# _sanitize_name
# =============================================================================


def test_sanitize_name_replaces_special_chars():
    assert _sanitize_name("test-name with spaces!") == "test_name_with_spaces"


def test_sanitize_name_strips_leading_trailing_underscores():
    assert _sanitize_name("---hello---") == "hello"


def test_sanitize_name_empty_string_returns_unnamed():
    assert _sanitize_name("") == "unnamed"


def test_sanitize_name_all_special_chars_returns_unnamed():
    assert _sanitize_name("!@#$%") == "unnamed"


def test_sanitize_name_leading_digit_gets_prefix():
    assert _sanitize_name("123abc") == "test_123abc"


def test_sanitize_name_lowercases():
    assert _sanitize_name("Hello_World") == "hello_world"


# =============================================================================
# _render_test
# =============================================================================


def _make_entry(**overrides) -> dict:
    base = {
        "test_name": "test_ssn_redacted",
        "scenario": "SSN in response",
        "expected": "[REDACTED]",
        "actual_response": "Your SSN is 123-45-6789",
        "policy_config": {
            "instructions": "Redact SSNs",
            "model": "claude-haiku-4-5",
        },
        "input_messages": [{"role": "user", "content": "What is my SSN?"}],
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_render_test_function_name_includes_test_name():
    seen: set[str] = set()
    body = _render_test(_make_entry(), seen)
    assert "async def test_test_ssn_redacted_regression" in body


def test_render_test_deduplicates_names():
    seen: set[str] = set()
    body1 = _render_test(_make_entry(), seen)
    body2 = _render_test(_make_entry(), seen)
    assert "test_test_ssn_redacted_regression(" in body1
    assert "test_test_ssn_redacted_regression_1(" in body2


def test_render_test_enqueues_actual_response():
    seen: set[str] = set()
    entry = _make_entry(actual_response="some llm output")
    body = _render_test(entry, seen)
    assert '"some llm output"' in body


def test_render_test_assertion_uses_expected_field():
    seen: set[str] = set()
    entry = _make_entry(expected="[REDACTED]")
    body = _render_test(entry, seen)
    assert '"[REDACTED]" in turn.text' in body


def test_render_test_judge_policy_enqueues_judge_response():
    seen: set[str] = set()
    entry = _make_entry()
    body = _render_test(entry, seen)
    assert "judge_replace_text" in body


def test_render_test_non_judge_policy_no_judge_response():
    seen: set[str] = set()
    entry = _make_entry(policy_config={"some_other_key": "value"})
    body = _render_test(entry, seen)
    assert "judge_replace_text" not in body


def test_render_test_extracts_user_content_from_messages():
    seen: set[str] = set()
    entry = _make_entry(input_messages=[{"role": "user", "content": "Tell me a secret"}])
    body = _render_test(entry, seen)
    assert '"Tell me a secret"' in body


def test_render_test_extracts_user_content_from_block_format():
    seen: set[str] = set()
    entry = _make_entry(
        input_messages=[
            {"role": "user", "content": [{"type": "text", "text": "Block content"}]}
        ]
    )
    body = _render_test(entry, seen)
    assert '"Block content"' in body


def test_render_test_generates_valid_python():
    seen: set[str] = set()
    body = _render_test(_make_entry(), seen)
    # Minimal preamble to make the generated code parseable
    preamble = (
        "import pytest\n"
        "from unittest.mock import MagicMock, AsyncMock\n"
        "mock_anthropic = MagicMock()\ngateway_healthy = None\n"
        "GATEWAY_URL = 'http://localhost:8000'\nAPI_KEY = 'test'\n"
        "SIMPLE_LLM_POLICY = 'x'\n"
        "def text_response(x): return x\n"
        "def judge_replace_text(x): return x\n"
        "def policy_context(a, b): pass\n"
        "MockAnthropicServer = MagicMock\n"
        "class ClaudeCodeSimulator:\n"
        "  def __init__(self, *a): pass\n"
        "  async def send(self, *a): pass\n\n"
    )
    compile(preamble + body, "<generated>", "exec")


# =============================================================================
# load_entries
# =============================================================================


def test_load_entries_reads_list_json(tmp_path):
    entries = [{"test_name": "t1"}, {"test_name": "t2"}]
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(entries))

    result = load_entries([path])

    assert len(result) == 2
    assert result[0]["test_name"] == "t1"


def test_load_entries_wraps_single_dict(tmp_path):
    entry = {"test_name": "t1"}
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(entry))

    result = load_entries([path])

    assert len(result) == 1


def test_load_entries_skips_invalid_json(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("not json {")

    result = load_entries([path])

    assert result == []
    assert "WARNING: skipping" in capsys.readouterr().err


# =============================================================================
# generate (integration)
# =============================================================================


def test_generate_produces_valid_output():
    entry = {
        "test_name": "test_foo",
        "scenario": "foo scenario",
        "expected": "foo",
        "actual_response": "foo response",
        "policy_config": {
            "instructions": "check for foo",
        },
        "input_messages": [{"role": "user", "content": "hello"}],
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    source = generate([entry])

    assert "test_test_foo_regression" in source
    assert "foo response" in source
    assert "Auto-generated" in source


def test_generate_empty_entries_returns_header_only():
    source = generate([])
    assert "Auto-generated" in source
    assert "async def" not in source
