#!/usr/bin/env python3
"""Export Claude Code session JSONL files to CSV format for Luthien import.

Usage:
    uv run python scripts/export_claude_sessions.py [--output-dir DIR] [--session-id UUID]

By default, exports all sessions from ~/.claude/projects/ subdirectories
to /Users/scottwofford/build/luthien-private-session-logs/

Filters out test sessions (short prompts, "say X", warmup, etc.)
"""

import argparse
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path


# Patterns that indicate test sessions (case-insensitive)
TEST_PATTERNS = [
    r"^say\s+['\"]?\w+['\"]?\.?$",  # "Say 'one'", "Say hello"
    r"^say\s+hello\s*briefly\.?$",  # "Say hello briefly"
    r"^what\s+is\s+\d+\s*\+\s*\d+",  # "What is 2+2?"
    r"^what\s+is\s+the\s+capital\s+of",  # "What is the capital of France?"
    r"^reply\s+with\s+just",  # "Reply with just the number"
    r"^warmup$",  # Agent warmup sessions
    r"^test$",
]

# Minimum content length for a real session (chars)
MIN_FIRST_MESSAGE_LENGTH = 25


def is_test_session(first_message: str) -> bool:
    """Check if a session appears to be a test based on first message."""
    if not first_message:
        return True

    msg = first_message.strip()

    # Too short to be real work
    if len(msg) < MIN_FIRST_MESSAGE_LENGTH:
        return True

    # Check against test patterns
    for pattern in TEST_PATTERNS:
        if re.match(pattern, msg, re.IGNORECASE):
            return True

    return False


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO timestamp string to datetime."""
    # Handle formats like "2026-01-25T18:50:09.347Z"
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def format_timestamp(dt: datetime) -> str:
    """Format datetime for CSV output."""
    return dt.strftime("%Y-%m-%d %H:%M:%S+00")


def extract_text_content(content: list | str) -> str:
    """Extract text from message content (could be string or list of blocks)."""
    if isinstance(content, str):
        return content

    texts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                # Include tool use as a summary
                tool_name = block.get("name", "unknown")
                texts.append(f"[Tool: {tool_name}]")
        elif isinstance(block, str):
            texts.append(block)

    return "\n".join(texts)


def get_session_title(messages: list[dict]) -> str:
    """Extract a title from the first user message."""
    for msg in messages:
        if msg.get("type") == "user":
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str):
                # Clean up and truncate
                title = content.strip()[:100]
                # Remove special characters for filename
                title = re.sub(r"[^\w\s-]", "", title)
                title = re.sub(r"\s+", "-", title).strip("-")
                return title[:50] or "untitled"
    return "untitled"


def get_session_date(messages: list[dict]) -> str:
    """Get the date from the first message timestamp."""
    for msg in messages:
        ts = msg.get("timestamp")
        if ts:
            dt = parse_timestamp(ts)
            return dt.strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def convert_session_to_csv(
    session_path: Path, output_dir: Path, skip_tests: bool = True
) -> tuple[Path | None, str]:
    """Convert a single session JSONL to CSV format.

    Returns:
        Tuple of (output_path or None, skip_reason or "")
    """
    messages = []

    with open(session_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                messages.append(entry)
            except json.JSONDecodeError:
                continue

    if not messages:
        return None, "empty"

    # Filter to user and assistant messages only
    conversation = []
    seen_content = set()  # Deduplicate streaming chunks
    first_user_message = None

    for entry in messages:
        entry_type = entry.get("type")
        timestamp = entry.get("timestamp")

        if entry_type == "user":
            content = entry.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                if first_user_message is None:
                    first_user_message = content.strip()
                if content not in seen_content:
                    seen_content.add(content)
                    conversation.append({
                        "timestamp": timestamp,
                        "role": "PROMPT",
                        "content": content
                    })

        elif entry_type == "assistant":
            msg = entry.get("message", {})
            content_blocks = msg.get("content", [])
            text = extract_text_content(content_blocks)

            # Only include if there's actual text (not just tool calls)
            if text.strip() and not text.startswith("[Tool:"):
                content_key = text[:200]  # Use first 200 chars as key
                if content_key not in seen_content:
                    seen_content.add(content_key)
                    conversation.append({
                        "timestamp": timestamp,
                        "role": "RESPONSE",
                        "content": text
                    })

    if len(conversation) < 2:
        # Need at least one prompt and one response
        return None, "no_content"

    # Check if this is a test session
    if skip_tests and is_test_session(first_user_message or ""):
        return None, "test_session"

    # Generate output filename
    session_date = get_session_date(messages)
    session_title = get_session_title(messages)
    session_id = session_path.stem  # UUID from filename

    output_filename = f"{session_date}_{session_title}_{session_id[:8]}.csv"
    output_path = output_dir / output_filename

    # Write CSV with UTF-8 BOM for proper encoding
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        # Write header comments
        f.write(f"Start_session description: {session_title},,,,\n")
        f.write("End_session_description:,,,,\n")
        f.write(",,,,\n")

        # Write CSV header and data
        writer = csv.writer(f)
        writer.writerow(["logged_by_luthien", "created_at", "prompt_or_response", "comments", "content"])

        for msg in conversation:
            ts = parse_timestamp(msg["timestamp"]) if msg.get("timestamp") else datetime.now()
            writer.writerow([
                "N",  # Not logged by Luthien
                format_timestamp(ts),
                msg["role"],
                "",  # No comments
                msg["content"]
            ])

    return output_path, ""


def list_existing_csvs(output_dir: Path) -> set[str]:
    """Get set of session IDs that already have CSVs."""
    existing = set()
    for csv_file in output_dir.glob("*.csv"):
        # Check if filename contains a UUID pattern (8 hex chars at end before .csv)
        match = re.search(r"_([a-f0-9]{8})\.csv$", csv_file.name)
        if match:
            existing.add(match.group(1))
    return existing


def main():
    parser = argparse.ArgumentParser(description="Export Claude Code sessions to CSV")
    parser.add_argument(
        "--sessions-dir",
        default=os.path.expanduser("~/.claude/projects/-Users-scottwofford-build-luthien-proxy"),
        help="Directory containing session JSONL files"
    )
    parser.add_argument(
        "--output-dir",
        default="/Users/scottwofford/build/luthien-private-session-logs",
        help="Output directory for CSV files"
    )
    parser.add_argument(
        "--session-id",
        help="Export only this specific session UUID"
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip sessions that already have CSV exports"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be exported without creating files"
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test sessions (short prompts, 'say X', etc.)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show details about skipped sessions"
    )

    args = parser.parse_args()

    sessions_dir = Path(args.sessions_dir)
    output_dir = Path(args.output_dir)

    if not sessions_dir.exists():
        print(f"Error: Sessions directory not found: {sessions_dir}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    # Get existing exports if skipping
    existing_ids = set()
    if args.skip_existing:
        existing_ids = list_existing_csvs(output_dir)
        print(f"Found {len(existing_ids)} existing CSV exports")

    # Find session files to process
    if args.session_id:
        session_files = list(sessions_dir.glob(f"{args.session_id}*.jsonl"))
    else:
        session_files = list(sessions_dir.glob("*.jsonl"))

    print(f"Found {len(session_files)} session files")

    exported = 0
    skipped = 0
    failed = 0

    for session_path in sorted(session_files):
        session_id = session_path.stem
        short_id = session_id[:8]

        if args.skip_existing and short_id in existing_ids:
            skipped += 1
            continue

        if args.dry_run:
            print(f"Would export: {session_path.name}")
            exported += 1
            continue

        try:
            output_path, skip_reason = convert_session_to_csv(
                session_path, output_dir, skip_tests=not args.include_tests
            )
            if output_path:
                print(f"Exported: {output_path.name}")
                exported += 1
            else:
                if args.verbose:
                    print(f"Skipped ({skip_reason}): {session_path.name}")
                skipped += 1
        except Exception as e:
            print(f"Failed: {session_path.name} - {e}")
            failed += 1

    print(f"\nSummary: {exported} exported, {skipped} skipped, {failed} failed")
    return 0


if __name__ == "__main__":
    exit(main())
