"""Assemble changelog fragments into CHANGELOG.md.

Reads .md files from changelog.d/, sorts by PR number, inserts them
under the ## Unreleased section, and deletes the consumed fragments.

Usage: uv run python scripts/assemble_changelog.py [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS_DIR = REPO_ROOT / "changelog.d"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

UNRELEASED_HEADER = "## Unreleased | TBA"
SKIP_FILES = {"README.md", ".gitkeep"}


def parse_pr_number(filename: str) -> int | None:
    """Extract leading PR number from a fragment filename like '187-client-setup.md'."""
    match = re.match(r"^(\d+)", filename)
    return int(match.group(1)) if match else None


def collect_fragments() -> list[tuple[int | None, Path]]:
    """Return fragment files sorted by PR number (unnumbered files sort last)."""
    fragments = []
    for path in FRAGMENTS_DIR.glob("*.md"):
        if path.name in SKIP_FILES:
            continue
        pr_num = parse_pr_number(path.name)
        fragments.append((pr_num, path))

    # Sort by PR number; files without a number go to the end
    fragments.sort(key=lambda item: (item[0] is None, item[0] or 0))
    return fragments


def read_fragment_content(path: Path) -> str:
    """Read a fragment file, stripping leading/trailing whitespace."""
    return path.read_text().strip()


def already_contains(changelog: str, content: str) -> bool:
    """Check if the changelog already contains this fragment's content (idempotency)."""
    # Compare the first non-empty line as a fingerprint
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return first_line != "" and first_line in changelog


def assemble(dry_run: bool = False) -> None:
    fragments = collect_fragments()
    if not fragments:
        print("No changelog fragments found.")
        return

    changelog = CHANGELOG_PATH.read_text()

    header_pos = changelog.find(UNRELEASED_HEADER)
    if header_pos == -1:
        print(f"ERROR: Could not find '{UNRELEASED_HEADER}' in CHANGELOG.md", file=sys.stderr)
        sys.exit(1)

    insertion_point = header_pos + len(UNRELEASED_HEADER)

    new_entries: list[str] = []
    files_to_delete: list[Path] = []

    for _, path in fragments:
        content = read_fragment_content(path)
        if not content:
            continue
        if already_contains(changelog, content):
            print(f"  skip (already present): {path.name}")
            files_to_delete.append(path)
            continue
        new_entries.append(content)
        files_to_delete.append(path)
        print(f"  add: {path.name}")

    if not new_entries:
        print("All fragments already present in CHANGELOG.md. Cleaning up files.")
        if not dry_run:
            for path in files_to_delete:
                path.unlink()
        return

    # Build the block to insert: blank line, then each entry separated by blank lines
    insert_block = "\n\n" + "\n\n".join(new_entries)

    # If there's already content after the header, keep a blank line separator
    after_header = changelog[insertion_point:]
    if after_header.lstrip("\n"):
        insert_block += "\n"

    updated = changelog[:insertion_point] + insert_block + after_header

    if dry_run:
        print("\n--- DRY RUN: would produce ---")
        # Show just the unreleased section (first 40 lines after header)
        start = updated.find(UNRELEASED_HEADER)
        preview_lines = updated[start:].splitlines()[:40]
        print("\n".join(preview_lines))
        print("...")
    else:
        CHANGELOG_PATH.write_text(updated)
        for path in files_to_delete:
            path.unlink()
        print(f"\nInserted {len(new_entries)} fragment(s) into CHANGELOG.md and cleaned up files.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble changelog fragments into CHANGELOG.md")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying files")
    args = parser.parse_args()
    assemble(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
