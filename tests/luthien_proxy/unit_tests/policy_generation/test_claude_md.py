# ABOUTME: Tests for CLAUDE.md -> policy YAML generation
# ABOUTME: Covers rule extraction heuristics, YAML rendering, round-trip loading, and the CLI

"""Tests for luthien_proxy.policy_generation.claude_md."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from luthien_proxy.config import load_policy_from_yaml
from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
from luthien_proxy.policy_generation import claude_md
from luthien_proxy.policy_generation.claude_md import (
    ExtractedRule,
    extract_rules,
    generate_policy_yaml,
    main,
)

REPO_ROOT = Path(__file__).parents[4]

SAMPLE_CLAUDE_MD = """\
# Project Guidelines

## Setup

- Run `npm install` to get started.
- The dev server lives at localhost:3000.

## Coding Rules

- Never commit secrets or API keys.
- Always write unit tests when adding new code.
- Prefer f-strings over .format() for readability.
- Nice weather today.

```bash
# Never run this inside a fence — it should be skipped
always_skip_me --must
```

**Do not edit generated files by hand.** They are rebuilt on every release
and manual edits will be lost.

> Never store credentials in a blockquote either.

| Column | Never used |
|--------|------------|
| a      | must skip  |
"""


class TestExtractRules:
    def test_extracts_normative_bullets(self):
        texts = [r.text for r in extract_rules(SAMPLE_CLAUDE_MD).rules]

        assert "Never commit secrets or API keys." in texts
        assert "Always write unit tests when adding new code." in texts
        assert "Prefer f-strings over .format() for readability." in texts

    def test_skips_non_normative_and_trivia_lines(self):
        texts = [r.text for r in extract_rules(SAMPLE_CLAUDE_MD).rules]

        assert not any("npm install" in t for t in texts)
        assert not any("localhost:3000" in t for t in texts)
        assert not any("Nice weather" in t for t in texts)

    def test_skips_fenced_code_blocks(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD).rules
        assert not any("always_skip_me" in r.text for r in rules)

    def test_skips_table_rows(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD).rules
        assert not any("must skip" in r.text for r in rules)

    def test_extracts_blockquote_rules(self):
        texts = [r.text for r in extract_rules(SAMPLE_CLAUDE_MD).rules]
        assert "Never store credentials in a blockquote either." in texts

    def test_normative_rule_starting_with_command_word_is_kept(self):
        doc = "git rebase should never be run interactively in this repo.\n"
        texts = [r.text for r in extract_rules(doc).rules]
        assert texts == ["git rebase should never be run interactively in this repo."]

    def test_paragraph_rules_join_continuation_lines(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD).rules
        para = next(r for r in rules if r.text.startswith("Do not edit generated files"))
        assert "manual edits will be lost" in para.text

    def test_line_numbers_are_one_based_and_correct(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD).rules
        never_rule = next(r for r in rules if r.text.startswith("Never commit secrets"))
        lines = SAMPLE_CLAUDE_MD.splitlines()
        assert "Never commit secrets" in lines[never_rule.line - 1]

    def test_deduplicates_case_insensitively(self):
        doc = "- Never push to main.\n\n- never push to MAIN.\n"
        assert len(extract_rules(doc).rules) == 1

    def test_strips_markdown_decoration(self):
        doc = "- **Always** use [uv](https://docs.astral.sh/uv/) and `pytest` for tests.\n"
        rules = extract_rules(doc).rules
        assert rules[0].text == "Always use uv and pytest for tests."

    def test_empty_document_yields_no_rules(self):
        assert extract_rules("").rules == ()
        assert extract_rules("# Just a heading\n\nSome plain prose.\n").rules == ()

    def test_overlong_rules_are_counted_not_silently_dropped(self):
        doc = "- Never " + "x" * 500 + "\n"
        result = extract_rules(doc)
        assert result.rules == ()
        assert result.skipped_too_long == 1

    def test_max_rule_chars_override_recovers_long_rules(self):
        doc = "- Never " + "x" * 500 + "\n"
        result = extract_rules(doc, max_rule_chars=1000)
        assert len(result.rules) == 1
        assert result.skipped_too_long == 0


class TestGeneratePolicyYaml:
    def _rules(self) -> list[ExtractedRule]:
        return [
            ExtractedRule(text="Never commit secrets.", line=7),
            ExtractedRule(text="Always write tests.", line=9),
        ]

    def test_rejects_empty_rules(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text("# empty\n")
        with pytest.raises(ValueError):
            generate_policy_yaml([], source)

    def test_yaml_structure_and_traceability(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        text = generate_policy_yaml(self._rules(), source)

        parsed = yaml.safe_load(text)
        policy = parsed["policy"]
        assert policy["class"] == "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
        instructions = policy["config"]["instructions"]
        assert "1. [CLAUDE.md:7] Never commit secrets." in instructions
        assert "2. [CLAUDE.md:9] Always write tests." in instructions

    def test_header_uses_file_name_not_absolute_path(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        text = generate_policy_yaml(self._rules(), source)

        header = text.split("policy:")[0]
        assert str(tmp_path) not in header

    def test_model_and_on_error_options(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        text = generate_policy_yaml(self._rules(), source, model="claude-sonnet-4-5", on_error="block")

        parsed = yaml.safe_load(text)
        assert parsed["policy"]["config"]["model"] == "claude-sonnet-4-5"
        assert parsed["policy"]["config"]["on_error"] == "block"

    def test_source_text_param_avoids_reading_file(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"  # never written to disk
        text = generate_policy_yaml(self._rules(), source, source_text=SAMPLE_CLAUDE_MD)
        assert yaml.safe_load(text)["policy"]["config"]["instructions"]

    def test_round_trip_through_policy_loader(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        out = tmp_path / "policy.yaml"
        out.write_text(generate_policy_yaml(extract_rules(SAMPLE_CLAUDE_MD).rules, source))

        policy = load_policy_from_yaml(str(out))

        assert isinstance(policy, SimpleLLMPolicy)


class TestAgainstRepoAgentsMd:
    """Realistic example: the generator run on this repo's own AGENTS.md."""

    def test_repo_agents_md_generates_loadable_policy(self, tmp_path: Path):
        source = REPO_ROOT / "AGENTS.md"
        rules = extract_rules(source.read_text(encoding="utf-8")).rules

        # Loose bounds: AGENTS.md evolves, but it is rule-dense.
        assert len(rules) >= 10

        out = tmp_path / "policy.yaml"
        out.write_text(generate_policy_yaml(rules, source))
        policy = load_policy_from_yaml(str(out))
        assert isinstance(policy, SimpleLLMPolicy)

        # Every rule is tagged back to a source line.
        instructions = yaml.safe_load(out.read_text())["policy"]["config"]["instructions"]
        assert instructions.count("[AGENTS.md:") == len(rules)


class TestCli:
    def _write_sample(self, tmp_path: Path) -> Path:
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        return source

    def test_writes_output_file(self, tmp_path: Path):
        source = self._write_sample(tmp_path)
        out = tmp_path / "policy.yaml"

        assert main([str(source), "-o", str(out)]) == 0
        assert isinstance(load_policy_from_yaml(str(out)), SimpleLLMPolicy)

    def test_stdout_mode_prints_yaml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = self._write_sample(tmp_path)

        assert main([str(source)]) == 0
        printed = capsys.readouterr().out
        assert yaml.safe_load(printed)["policy"]["class"].endswith("SimpleLLMPolicy")

    def test_model_and_on_error_flags_reach_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = self._write_sample(tmp_path)

        assert main([str(source), "--model", "claude-sonnet-4-5", "--on-error", "block"]) == 0
        config = yaml.safe_load(capsys.readouterr().out)["policy"]["config"]
        assert config["model"] == "claude-sonnet-4-5"
        assert config["on_error"] == "block"

    def test_no_validate_skips_loader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        source = self._write_sample(tmp_path)

        def boom(_yaml_text: str) -> None:
            raise AssertionError("validation should not run with --no-validate")

        monkeypatch.setattr(claude_md, "_validate_policy_yaml", boom)
        assert main([str(source), "--no-validate"]) == 0

    def test_validation_failure_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        source = self._write_sample(tmp_path)

        def boom(_yaml_text: str) -> None:
            raise ValueError("synthetic loader failure")

        monkeypatch.setattr(claude_md, "_validate_policy_yaml", boom)
        assert main([str(source)]) == 2
        assert "failed policy-loader validation" in capsys.readouterr().err

    def test_skipped_long_rules_reported_on_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = tmp_path / "CLAUDE.md"
        source.write_text("- Never push to main.\n\n- Never " + "x" * 500 + "\n")

        assert main([str(source)]) == 0
        assert "skipped 1 rule candidate(s)" in capsys.readouterr().err

    def test_max_rule_chars_flag(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = tmp_path / "CLAUDE.md"
        source.write_text("- Never " + "x" * 500 + "\n")

        assert main([str(source), "--max-rule-chars", "1000"]) == 0
        captured = capsys.readouterr()
        assert "skipped" not in captured.err
        assert yaml.safe_load(captured.out)["policy"]["config"]["instructions"]

    def test_missing_input_fails(self, tmp_path: Path):
        assert main([str(tmp_path / "nope.md")]) == 1

    def test_no_rules_found_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = tmp_path / "CLAUDE.md"
        source.write_text("# heading only\n\nplain prose without normative language\n")

        assert main([str(source)]) == 1
        assert "no enforceable rules" in capsys.readouterr().err
