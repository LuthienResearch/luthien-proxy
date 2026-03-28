"""Unit tests for scripts/generate_mock_from_failures.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import generate_mock_from_failures as mod
from generate_mock_from_failures import _load_entries, _render_test, _safe_name, generate


# =============================================================================
# _safe_name
# =============================================================================


def test_safe_name_replaces_special_chars():
    assert _safe_name("test-name with spaces!") == "test_name_with_spaces"


def test_safe_name_strips_leading_trailing_underscores():
    assert _safe_name("---hello---") == "hello"


def test_safe_name_empty_string_returns_fallback():
    assert _safe_name("") == "failure"


def test_safe_name_all_special_chars_returns_fallback():
    assert _safe_name("!@#$%") == "failure"


def test_safe_name_truncates_at_60():
    long = "a" * 100
    assert len(_safe_name(long)) == 60


def test_safe_name_custom_fallback():
    assert _safe_name("", fallback="custom") == "custom"


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
            "class_ref": "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy",
            "model": "claude-haiku-4-5",
        },
        "timestamp": "2026-01-01T00:00:00+00:00",
        "_source_file": "test_ssn_redacted_20260101_000000.json",
    }
    base.update(overrides)
    return base


def test_render_test_function_name_includes_index_and_test_name():
    body = _render_test(_make_entry(), 0)
    assert "async def test_mock_regression_000_test_ssn_redacted" in body


def test_render_test_enqueues_actual_response():
    entry = _make_entry(actual_response="some llm output")
    body = _render_test(entry, 0)
    assert "'some llm output'" in body


def test_render_test_assertion_uses_expected_field():
    entry = _make_entry(expected="[REDACTED]")
    body = _render_test(entry, 0)
    assert "'[REDACTED]' in content" in body


def test_render_test_missing_expected_generates_skip():
    entry = _make_entry()
    del entry["expected"]
    body = _render_test(entry, 0)
    assert "pytest.skip" in body
    assert "in content" not in body


def test_render_test_empty_expected_generates_skip():
    entry = _make_entry(expected="")
    body = _render_test(entry, 0)
    assert "pytest.skip" in body
    assert "in content" not in body


def test_render_test_strips_class_ref_from_config():
    entry = _make_entry()
    body = _render_test(entry, 0)
    assert "class_ref" not in body.split("policy_context")[1].split(")")[0]


def test_render_test_missing_policy_config_uses_default_class():
    entry = _make_entry()
    del entry["policy_config"]
    body = _render_test(entry, 0)
    assert "SimpleLLMPolicy" in body


_COMPILE_PREAMBLE = (
    "import pytest\nimport httpx\nfrom unittest.mock import MagicMock\n"
    "mock_anthropic = MagicMock()\ngateway_healthy = None\n"
    "GATEWAY_URL = 'http://localhost:8000'\n"
    "_BASE_REQUEST = {}\n_HEADERS = {}\n"
    "def text_response(x): return x\n"
    "def policy_context(a, b): pass\n"
    "MockAnthropicServer = MagicMock\n\n"
)


def test_render_test_generates_valid_python():
    body = _render_test(_make_entry(), 0)
    compile(_COMPILE_PREAMBLE + body, "<generated>", "exec")


def test_render_test_adversarial_actual_response_produces_valid_python():
    adversarial = "it's a \"test\" with\nnewlines and '''triple quotes'''"
    entry = _make_entry(actual_response=adversarial)
    body = _render_test(entry, 0)
    compile(_COMPILE_PREAMBLE + body, "<generated>", "exec")


# =============================================================================
# _load_entries
# =============================================================================


def test_load_entries_reads_list_json(tmp_path, monkeypatch):
    entries = [{"test_name": "t1"}, {"test_name": "t2"}]
    (tmp_path / "capture.json").write_text(json.dumps(entries))
    monkeypatch.setattr(mod, "REGISTRY_DIR", tmp_path)

    result = _load_entries()

    assert len(result) == 2
    assert result[0]["test_name"] == "t1"
    assert result[0]["_source_file"] == "capture.json"


def test_load_entries_wraps_single_dict(tmp_path, monkeypatch):
    entry = {"test_name": "t1"}
    (tmp_path / "capture.json").write_text(json.dumps(entry))
    monkeypatch.setattr(mod, "REGISTRY_DIR", tmp_path)

    result = _load_entries()

    assert len(result) == 1


def test_load_entries_skips_invalid_json(tmp_path, capsys, monkeypatch):
    (tmp_path / "bad.json").write_text("not json {")
    monkeypatch.setattr(mod, "REGISTRY_DIR", tmp_path)

    result = _load_entries()

    assert result == []
    assert "skipping bad.json" in capsys.readouterr().err


# =============================================================================
# generate (integration)
# =============================================================================


def test_generate_writes_output_file(tmp_path, monkeypatch):
    registry = tmp_path / "failure_registry"
    registry.mkdir()
    entry = {
        "test_name": "test_foo",
        "scenario": "foo scenario",
        "expected": "foo",
        "actual_response": "foo response",
        "policy_config": {
            "class_ref": "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy",
        },
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    (registry / "capture.json").write_text(json.dumps([entry]))
    monkeypatch.setattr(mod, "REGISTRY_DIR", registry)

    output = tmp_path / "test_mock_from_failures.py"
    rc = generate(output)

    assert rc == 0
    content = output.read_text()
    assert "test_mock_regression_000_test_foo" in content
    assert "foo response" in content


def test_generate_returns_1_when_registry_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REGISTRY_DIR", tmp_path / "nonexistent")
    output = tmp_path / "out.py"

    assert generate(output) == 1


def test_generate_returns_0_when_no_entries(tmp_path, monkeypatch):
    registry = tmp_path / "failure_registry"
    registry.mkdir()
    monkeypatch.setattr(mod, "REGISTRY_DIR", registry)
    output = tmp_path / "out.py"

    assert generate(output) == 0
    assert not output.exists()
