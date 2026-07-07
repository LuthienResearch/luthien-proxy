"""Generate a Luthien policy YAML from a CLAUDE.md / AGENTS.md file.

Reads a CLAUDE.md, extracts enforceable behavioral rules with a deterministic
heuristic (no LLM call), and emits a `SimpleLLMPolicy` configuration that
`luthien_proxy.config.load_policy_from_yaml` accepts. Every extracted rule is
tagged with its source line number so judge decisions stay traceable back to
the originating CLAUDE.md text.

Why heuristic extraction: it is deterministic (same input -> same policy),
needs no credentials at generation time, and keeps rule provenance exact.
LLM-assisted extraction can layer on top later without changing the output
format.

Usage:

    uv run python -m luthien_proxy.policy_generation.claude_md CLAUDE.md
    uv run python -m luthien_proxy.policy_generation.claude_md CLAUDE.md -o config/claude_md_policy.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from luthien_proxy.config import load_policy_from_yaml

DEFAULT_MODEL = "claude-haiku-4-5"

# A candidate line must contain at least one normative marker to count as an
# enforceable rule (vs. repo trivia like directory listings or build commands).
_NORMATIVE_PATTERN = re.compile(
    r"""
    \b(
        never
        | always
        | must(\ not)?
        | do\ not
        | don'?t
        | avoid
        | prefer(red)?
        | required?
        | forbidden
        | disallowed
        | ban(ned)?
        | should(\ not)?
        | ensure
        | instead\ of
        | only\ (use|if|when)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BULLET_PATTERN = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+(.*)$")
_FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")
_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s")
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BLOCKQUOTE_PATTERN = re.compile(r"^(\s*)>\s?")

# Rules shorter than this (after markdown stripping) are fragments, not rules.
_MIN_RULE_CHARS = 12
# Rules longer than this are prose sections, not individually enforceable rules.
_MAX_RULE_CHARS = 400


@dataclass(frozen=True)
class ExtractedRule:
    """A behavioral rule extracted from a CLAUDE.md file.

    Attributes:
        text: The rule text with markdown decoration stripped.
        line: 1-based line number in the source file where the rule starts.
    """

    text: str
    line: int


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of a rule-extraction pass.

    Attributes:
        rules: Extracted rules in document order.
        skipped_too_long: Normative candidates dropped for exceeding the
            rule-length cap (surfaced so long rules never vanish silently).
    """

    rules: tuple[ExtractedRule, ...]
    skipped_too_long: int


def _strip_markdown(text: str) -> str:
    """Remove markdown decoration, keeping the readable text."""
    text = _LINK_PATTERN.sub(r"\1", text)
    text = text.replace("**", "")
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class _Candidate:
    """A candidate rule being accumulated across continuation lines."""

    first_line: int
    parts: list[str]

    def text(self) -> str:
        return _strip_markdown(" ".join(self.parts))


def _is_candidate_break(line: str) -> bool:
    """True when a line terminates the current candidate paragraph/bullet."""
    stripped = line.strip()
    return not stripped or bool(_HEADING_PATTERN.match(line)) or stripped.startswith("|")


def extract_rules(markdown: str, *, max_rule_chars: int = _MAX_RULE_CHARS) -> ExtractionResult:
    """Extract enforceable behavioral rules from CLAUDE.md content.

    Walks the document line by line, skipping fenced code blocks, headings,
    and tables. Bullets, blockquotes, and short paragraphs qualify as rules
    when they carry a normative marker (never / always / must / avoid /
    prefer / ...). Rules keep the 1-based line number where they start.

    Args:
        markdown: Full text of a CLAUDE.md / AGENTS.md file.
        max_rule_chars: Candidates longer than this (after markdown stripping)
            are counted in `skipped_too_long` instead of extracted.

    Returns:
        Extraction result with rules in document order (deduplicated
        case-insensitively) plus a count of normative candidates skipped
        for exceeding `max_rule_chars`.
    """
    rules: list[ExtractedRule] = []
    seen: set[str] = set()
    in_fence = False
    candidate: _Candidate | None = None
    skipped_too_long = 0

    def flush(current: _Candidate | None) -> None:
        nonlocal skipped_too_long
        if current is None:
            return
        text = current.text()
        if len(text) < _MIN_RULE_CHARS:
            return
        if not _NORMATIVE_PATTERN.search(text):
            return
        if len(text) > max_rule_chars:
            skipped_too_long += 1
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        rules.append(ExtractedRule(text=text, line=current.first_line))

    for lineno, raw_line in enumerate(markdown.splitlines(), start=1):
        if _FENCE_PATTERN.match(raw_line):
            flush(candidate)
            candidate = None
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        # Blockquoted rules (callout style) participate like normal text.
        line = _BLOCKQUOTE_PATTERN.sub(r"\1", raw_line)

        if _is_candidate_break(line):
            flush(candidate)
            candidate = None
            continue

        bullet_match = _BULLET_PATTERN.match(line)
        if bullet_match:
            flush(candidate)
            candidate = _Candidate(first_line=lineno, parts=[bullet_match.group(2)])
            continue

        if candidate is not None:
            candidate.parts.append(line.strip())
        else:
            candidate = _Candidate(first_line=lineno, parts=[line.strip()])

    flush(candidate)
    return ExtractionResult(rules=tuple(rules), skipped_too_long=skipped_too_long)


def _build_instructions(rules: Sequence[ExtractedRule], source_name: str) -> str:
    """Compose judge instructions from extracted rules, tagged with source lines."""
    numbered = "\n".join(f"{i}. [{source_name}:{rule.line}] {rule.text}" for i, rule in enumerate(rules, start=1))
    return (
        "You are reviewing responses from an AI coding assistant. The project's "
        f"{source_name} defines behavioral rules the assistant must follow. "
        "Evaluate each content block against these rules (each rule is tagged "
        "with the source line it came from):\n\n"
        f"{numbered}\n\n"
        "If a block complies with every rule, return it unchanged. If a block "
        "violates a rule, rewrite it minimally so it complies. If a violation "
        "cannot be fixed by rewriting, replace the block with a brief note "
        "naming the violated rule and its source tag."
    )


class _LiteralDumper(yaml.SafeDumper):
    """SafeDumper that renders multiline strings as literal blocks (|-)."""


def _represent_multiline_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_LiteralDumper.add_representer(str, _represent_multiline_str)


def generate_policy_yaml(
    rules: Sequence[ExtractedRule],
    source_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    on_error: str = "pass",
    source_text: str | None = None,
) -> str:
    """Render a SimpleLLMPolicy YAML document from extracted rules.

    Args:
        rules: Rules extracted from the source file (must be non-empty).
        source_path: The CLAUDE.md file the rules came from (for provenance).
        model: Judge model identifier.
        on_error: Judge failure behavior ("pass" or "block").
        source_text: The source file's content, if the caller already read it
            (avoids a second read). Read from `source_path` when None.

    Returns:
        A YAML string loadable by `luthien_proxy.config.load_policy_from_yaml`.

    Raises:
        ValueError: If `rules` is empty.
    """
    if not rules:
        raise ValueError("Cannot generate a policy from zero rules")

    if source_text is None:
        source_text = source_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:12]

    document = {
        "policy": {
            "class": "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy",
            "config": {
                "model": model,
                "on_error": on_error,
                "inference_provider": "user_credentials",
                "instructions": _build_instructions(rules, source_path.name),
            },
        }
    }
    body = yaml.dump(document, Dumper=_LiteralDumper, sort_keys=False, width=100, allow_unicode=True)
    header = (
        f"# Luthien policy generated from {source_path.name}\n"
        f"# Source: {source_path.name} (sha256 {digest})\n"
        f"# Rules extracted: {len(rules)} (each tagged [{source_path.name}:<line>] below)\n"
        "# Regenerate: uv run python -m luthien_proxy.policy_generation.claude_md "
        f"{source_path.name}\n"
    )
    return header + body


def _validate_policy_yaml(yaml_text: str) -> None:
    """Round-trip the generated YAML through the real policy loader.

    Raises:
        Exception: Whatever `load_policy_from_yaml` raises for an invalid config.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
        handle.write(yaml_text)
        temp_path = handle.name
    try:
        load_policy_from_yaml(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: generate and validate a policy YAML from a CLAUDE.md."""
    parser = argparse.ArgumentParser(
        prog="python -m luthien_proxy.policy_generation.claude_md",
        description="Generate a Luthien SimpleLLMPolicy YAML from a CLAUDE.md file.",
    )
    parser.add_argument("input", type=Path, help="Path to CLAUDE.md / AGENTS.md")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write the policy YAML here (default: print to stdout)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Judge model (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--on-error",
        choices=("pass", "block"),
        default="pass",
        help="Judge failure behavior: pass content with a warning, or block it (default: pass)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip round-trip validation through the policy loader",
    )
    parser.add_argument(
        "--max-rule-chars",
        type=int,
        default=_MAX_RULE_CHARS,
        help=f"Skip rules longer than this many characters (default: {_MAX_RULE_CHARS})",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"error: {args.input} is not a file", file=sys.stderr)
        return 1

    source_text = args.input.read_text(encoding="utf-8")
    result = extract_rules(source_text, max_rule_chars=args.max_rule_chars)
    rules = result.rules
    if result.skipped_too_long:
        print(
            f"note: skipped {result.skipped_too_long} rule candidate(s) longer than "
            f"{args.max_rule_chars} characters (raise with --max-rule-chars)",
            file=sys.stderr,
        )
    if not rules:
        print(
            f"error: no enforceable rules found in {args.input}. "
            "The extractor looks for bullets/paragraphs with normative language "
            "(never / always / must / avoid / prefer / ...).",
            file=sys.stderr,
        )
        return 1

    yaml_text = generate_policy_yaml(
        rules,
        args.input,
        model=args.model,
        on_error=args.on_error,
        source_text=source_text,
    )

    if not args.no_validate:
        try:
            _validate_policy_yaml(yaml_text)
        except Exception as exc:
            print(f"error: generated YAML failed policy-loader validation: {exc}", file=sys.stderr)
            return 2

    if args.output is None:
        print(yaml_text, end="")
    else:
        args.output.write_text(yaml_text, encoding="utf-8")
        print(f"Wrote {args.output} ({len(rules)} rules extracted from {args.input})", file=sys.stderr)
        print(f"Activate it with: export POLICY_CONFIG={args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
