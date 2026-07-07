# ABOUTME: Tests for CLAUDE.md -> policy YAML generation
# ABOUTME: Covers rule extraction heuristics, YAML rendering, round-trip loading, and the CLI

"""Tests for luthien_proxy.policy_generation.claude_md."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from luthien_proxy.config import load_policy_from_yaml
from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
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

| Column | Never used |
|--------|------------|
| a      | must skip  |
"""


class TestExtractRules:
    def test_extracts_normative_bullets(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD)
        texts = [r.text for r in rules]

        assert "Never commit secrets or API keys." in texts
        assert "Always write unit tests when adding new code." in texts
        assert "Prefer f-strings over .format() for readability." in texts

    def test_skips_non_normative_and_trivia_lines(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD)
        texts = [r.text for r in rules]

        assert not any("npm install" in t for t in texts)
        assert not any("localhost:3000" in t for t in texts)
        assert not any("Nice weather" in t for t in texts)

    def test_skips_fenced_code_blocks(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD)
        assert not any("always_skip_me" in r.text for r in rules)

    def test_skips_table_rows(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD)
        assert not any("must skip" in r.text for r in rules)

    def test_paragraph_rules_join_continuation_lines(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD)
        para = next(r for r in rules if r.text.startswith("Do not edit generated files"))
        assert "manual edits will be lost" in para.text

    def test_line_numbers_are_one_based_and_correct(self):
        rules = extract_rules(SAMPLE_CLAUDE_MD)
        never_rule = next(r for r in rules if r.text.startswith("Never commit secrets"))
        lines = SAMPLE_CLAUDE_MD.splitlines()
        assert "Never commit secrets" in lines[never_rule.line - 1]

    def test_deduplicates_case_insensitively(self):
        doc = "- Never push to main.\n\n- never push to MAIN.\n"
        rules = extract_rules(doc)
        assert len(rules) == 1

    def test_strips_markdown_decoration(self):
        doc = "- **Always** use [uv](https://docs.astral.sh/uv/) and `pytest` for tests.\n"
        rules = extract_rules(doc)
        assert rules[0].text == "Always use uv and pytest for tests."

    def test_empty_document_yields_no_rules(self):
        assert extract_rules("") == []
        assert extract_rules("# Just a heading\n\nSome plain prose.\n") == []

    def test_overlong_paragraphs_are_skipped(self):
        doc = "- Never " + "x" * 500 + "\n"
        assert extract_rules(doc) == []


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

    def test_model_and_on_error_options(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        text = generate_policy_yaml(self._rules(), source, model="claude-sonnet-4-5", on_error="block")

        parsed = yaml.safe_load(text)
        assert parsed["policy"]["config"]["model"] == "claude-sonnet-4-5"
        assert parsed["policy"]["config"]["on_error"] == "block"

    def test_round_trip_through_policy_loader(self, tmp_path: Path):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        out = tmp_path / "policy.yaml"
        out.write_text(generate_policy_yaml(extract_rules(SAMPLE_CLAUDE_MD), source))

        policy = load_policy_from_yaml(str(out))

        assert isinstance(policy, SimpleLLMPolicy)
        assert "[CLAUDE.md:" in policy._config.instructions


class TestAgainstRepoAgentsMd:
    """Realistic example: the generator run on this repo's own AGENTS.md."""

    def test_repo_agents_md_generates_loadable_policy(self, tmp_path: Path):
        source = REPO_ROOT / "AGENTS.md"
        rules = extract_rules(source.read_text(encoding="utf-8"))

        # Loose bounds: AGENTS.md evolves, but it is rule-dense.
        assert len(rules) >= 10

        out = tmp_path / "policy.yaml"
        out.write_text(generate_policy_yaml(rules, source))
        policy = load_policy_from_yaml(str(out))

        assert isinstance(policy, SimpleLLMPolicy)
        # Every rule is tagged back to a source line.
        assert policy._config.instructions.count("[AGENTS.md:") == len(rules)


class TestCli:
    def test_writes_output_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)
        out = tmp_path / "policy.yaml"

        exit_code = main([str(source), "-o", str(out)])

        assert exit_code == 0
        assert isinstance(load_policy_from_yaml(str(out)), SimpleLLMPolicy)

    def test_stdout_mode_prints_yaml(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = tmp_path / "CLAUDE.md"
        source.write_text(SAMPLE_CLAUDE_MD)

        exit_code = main([str(source)])

        assert exit_code == 0
        printed = capsys.readouterr().out
        assert yaml.safe_load(printed)["policy"]["class"].endswith("SimpleLLMPolicy")

    def test_missing_input_fails(self, tmp_path: Path):
        assert main([str(tmp_path / "nope.md")]) == 1

    def test_no_rules_found_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        source = tmp_path / "CLAUDE.md"
        source.write_text("# heading only\n\nplain prose without normative language\n")

        assert main([str(source)]) == 1
        assert "no enforceable rules" in capsys.readouterr().err
