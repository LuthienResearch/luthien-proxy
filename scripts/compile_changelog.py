#!/usr/bin/env python3
"""Compile changelog fragments from changelog.d/ into CHANGELOG.md.

Each fragment is a markdown file with YAML frontmatter:

    ---
    category: Features
    pr: 123
    ---

    **My feature**: description here

Run with --dry-run to preview without modifying files.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS_DIR = REPO_ROOT / "changelog.d"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

CATEGORY_ORDER = ["Features", "Fixes", "Refactors", "Chores & Docs"]
UNRELEASED_HEADER = "## Unreleased"
SKIP_FILES = {"README.md", ".gitkeep"}


def parse_fragment(path: Path) -> dict[str, str]:
    """Parse a changelog fragment file into {category, body, pr?}."""
    text = path.read_text().strip()

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.+)$", text, re.DOTALL)
    if not match:
        sys.exit(f"Bad fragment format in {path.name} — missing YAML frontmatter")

    frontmatter_raw, body = match.group(1), match.group(2).strip()

    frontmatter: dict[str, str] = {}
    for line in frontmatter_raw.strip().splitlines():
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()

    category = frontmatter.get("category", "")
    if category not in CATEGORY_ORDER:
        sys.exit(f"Unknown category '{category}' in {path.name}. Use one of: {', '.join(CATEGORY_ORDER)}")

    return {"category": category, "body": body, "pr": frontmatter.get("pr", "")}


def collect_fragments() -> dict[str, list[str]]:
    """Read all fragments and group by category."""
    grouped: dict[str, list[str]] = {cat: [] for cat in CATEGORY_ORDER}

    fragment_files = sorted(p for p in FRAGMENTS_DIR.iterdir() if p.is_file() and p.name not in SKIP_FILES)

    if not fragment_files:
        return grouped

    for path in fragment_files:
        frag = parse_fragment(path)
        entry = frag["body"]
        if frag["pr"]:
            # Append PR link if not already present in body
            if f"#{frag['pr']}" not in entry:
                entry = f"{entry} (#{frag['pr']})"
        grouped[frag["category"]].append(entry)

    return grouped


def build_section(grouped: dict[str, list[str]]) -> str:
    """Build the markdown text for all new entries."""
    lines: list[str] = []
    for cat in CATEGORY_ORDER:
        entries = grouped[cat]
        if not entries:
            continue
        lines.append(f"### {cat}\n")
        for entry in entries:
            # Entries may be multi-line (with sub-bullets).
            # First line gets "- " prefix; subsequent lines are indented as-is.
            entry_lines = entry.splitlines()
            lines.append(f"- {entry_lines[0]}")
            for sub in entry_lines[1:]:
                lines.append(sub)
        lines.append("")  # blank line after category
    return "\n".join(lines)


def insert_into_changelog(new_section: str, dry_run: bool) -> None:
    """Insert compiled entries under the Unreleased header in CHANGELOG.md."""
    changelog = CHANGELOG_PATH.read_text()

    # Find the "## Unreleased" line
    unreleased_pattern = re.compile(r"^## Unreleased.*$", re.MULTILINE)
    match = unreleased_pattern.search(changelog)
    if not match:
        sys.exit(f"Could not find '{UNRELEASED_HEADER}' header in CHANGELOG.md")

    insert_pos = match.end()
    # Skip any blank lines immediately after the header
    rest = changelog[insert_pos:]
    leading_blanks = len(rest) - len(rest.lstrip("\n"))
    insert_pos += leading_blanks

    updated = changelog[:insert_pos] + "\n" + new_section + changelog[insert_pos:]

    if dry_run:
        print("--- Would insert into CHANGELOG.md: ---")
        print(new_section)
        print("--- End preview ---")
    else:
        CHANGELOG_PATH.write_text(updated)
        print(f"Updated {CHANGELOG_PATH.name}")


def delete_fragments() -> list[str]:
    """Remove compiled fragment files. Returns list of deleted filenames."""
    deleted = []
    for path in sorted(FRAGMENTS_DIR.iterdir()):
        if path.is_file() and path.name not in SKIP_FILES:
            path.unlink()
            deleted.append(path.name)
    return deleted


def cut_release(version: str, dry_run: bool) -> None:
    """Rename the Unreleased section to a versioned release section.

    Moves content from '## Unreleased' into '## <version> | <date>' and
    inserts a fresh empty '## Unreleased | TBA' header above it.
    Skips if a section for this version already exists.
    """
    changelog = CHANGELOG_PATH.read_text()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check if this version section already exists
    if re.search(rf"^## {re.escape(version)}(\s|\|)", changelog, re.MULTILINE):
        print(f"Section for {version} already exists in CHANGELOG.md — skipping cut.")
        return

    match = re.search(r"^## Unreleased.*$", changelog, re.MULTILINE)
    if not match:
        sys.exit(f"Could not find '{UNRELEASED_HEADER}' header in CHANGELOG.md")

    body_start = match.end()
    while body_start < len(changelog) and changelog[body_start] == "\n":
        body_start += 1

    next_h2 = re.search(r"^## ", changelog[body_start:], re.MULTILINE)
    body_end = body_start + next_h2.start() if next_h2 else len(changelog)
    unreleased_body = changelog[body_start:body_end].strip()

    before = changelog[: match.start()].rstrip("\n")
    after = changelog[body_end:].lstrip("\n")

    parts = [before, "", "## Unreleased | TBA", ""]
    if unreleased_body:
        parts += [f"## {version} | {today}", "", unreleased_body, ""]
    else:
        parts += [f"## {version} | {today}", ""]

    if after:
        parts.append(after)

    result = "\n".join(parts)

    if dry_run:
        print(f"Would cut release {version} | {today}")
        if unreleased_body:
            print(f"  Moving {len(unreleased_body.splitlines())} lines to versioned section")
        else:
            print("  Unreleased section is empty")
    else:
        CHANGELOG_PATH.write_text(result)
        print(f"Cut release section: ## {version} | {today}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview compiled output without modifying files",
    )
    parser.add_argument(
        "--cut-release",
        metavar="VERSION",
        help="Cut a release: rename Unreleased section to VERSION (e.g. '3.0.0')",
    )
    args = parser.parse_args()

    if args.cut_release:
        cut_release(args.cut_release, dry_run=args.dry_run)
        return

    grouped = collect_fragments()
    total = sum(len(v) for v in grouped.values())

    if total == 0:
        print("No changelog fragments found.")
        return

    section = build_section(grouped)

    if args.dry_run:
        print(f"Found {total} fragment(s):\n")
        print(section)
    else:
        insert_into_changelog(section, dry_run=False)
        deleted = delete_fragments()
        print(f"Compiled {total} fragment(s), deleted: {', '.join(deleted)}")


if __name__ == "__main__":
    main()
