#!/usr/bin/env python3
"""Import session log CSVs into the conversation history database.

Usage:
    uv run python scripts/import_session_csvs.py /path/to/csv/dir

This reads CSV files from the private session logs directory and imports them
into the conversation_events and conversation_calls tables so they appear in
the /history UI.
"""

import asyncio
import csv
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import asyncpg
import json
from dotenv import load_dotenv


def parse_csv(filepath: Path) -> list[dict]:
    """Parse a session log CSV file."""
    rows = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        # Skip the Start_session description line
        first_line = f.readline()
        if not first_line.startswith("logged_by_luthien"):
            # It's the description line, read the header next
            pass
        else:
            # First line was already the header, seek back
            f.seek(0)

        # Find the actual header line
        for line in f:
            if line.startswith("logged_by_luthien"):
                break

        # Now read the CSV data
        reader = csv.DictReader(
            f,
            fieldnames=["logged_by_luthien", "created_at", "prompt_or_response", "comments", "content"],
        )
        for row in reader:
            # Skip empty rows or End_session_description
            if not row.get("created_at") or row.get("logged_by_luthien", "").startswith("End_"):
                continue
            rows.append(row)

    return rows


def derive_session_id(filepath: Path) -> str:
    """Derive a session ID from the filename."""
    # e.g., "2026-01-23_pr133-convo-hist-ui-tweaks.csv" -> "import-2026-01-23-pr133-convo-hist"
    name = filepath.stem  # Remove .csv
    # Make it look like a session ID
    return f"import-{name.replace('_', '-')[:40]}"


def derive_model_from_filename(filepath: Path) -> str:
    """Guess the model/tool from filename."""
    name = filepath.name.lower()
    if "codex" in name:
        return "codex"
    if "claude" in name or "cc" in name:
        return "claude-3-5-sonnet-20241022"
    return "claude-3-5-sonnet-20241022"  # Default


async def import_session(conn: asyncpg.Connection, filepath: Path, dry_run: bool = False) -> int:
    """Import a single CSV file as a session."""
    rows = parse_csv(filepath)
    if not rows:
        print(f"  Skipping {filepath.name}: no data rows found")
        return 0

    session_id = derive_session_id(filepath)
    model = derive_model_from_filename(filepath)

    # Check if session already exists
    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM conversation_events WHERE session_id = $1",
        session_id,
    )
    if existing > 0:
        print(f"  Skipping {filepath.name}: session '{session_id}' already imported ({existing} events)")
        return 0

    # Group rows into PROMPT/RESPONSE pairs (turns)
    # A turn = one PROMPT + all following RESPONSEs until next PROMPT
    turns = []
    current_turn = {"prompt": None, "response": None, "prompt_ts": None, "response_ts": None}

    for row in rows:
        msg_type = row.get("prompt_or_response", "").upper()
        content = row.get("content", "")
        timestamp_str = row.get("created_at", "")

        # Parse timestamp
        try:
            # Handle various formats
            timestamp_str = timestamp_str.strip()
            if "+" in timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace("+00", "+00:00"))
            else:
                timestamp = datetime.fromisoformat(timestamp_str)
        except (ValueError, AttributeError):
            timestamp = datetime.now()

        if msg_type == "PROMPT":
            # Save previous turn if it has content
            if current_turn["prompt"] is not None or current_turn["response"] is not None:
                turns.append(current_turn)
            # Start new turn
            current_turn = {"prompt": content, "response": None, "prompt_ts": timestamp, "response_ts": None}
        elif msg_type == "RESPONSE":
            # Accumulate responses - keep first response content, update timestamp to last
            if current_turn["response"] is None:
                current_turn["response"] = content
            else:
                current_turn["response"] += "\n\n" + content
            current_turn["response_ts"] = timestamp  # Always use latest response timestamp

    # Don't forget last turn
    if current_turn["prompt"] is not None or current_turn["response"] is not None:
        turns.append(current_turn)

    # Use prompt_ts as fallback for response_ts if no response
    for turn in turns:
        if turn["response_ts"] is None:
            turn["response_ts"] = turn["prompt_ts"]
        if turn["prompt_ts"] is None:
            turn["prompt_ts"] = turn["response_ts"]

    if not turns:
        print(f"  Skipping {filepath.name}: no valid turns found")
        return 0

    if dry_run:
        print(f"  Would import {filepath.name}: {len(turns)} turns as session '{session_id}'")
        return len(turns)

    # Insert turns into database
    events_inserted = 0
    for i, turn in enumerate(turns):
        call_id = f"{session_id}-turn-{i:04d}"
        prompt_ts = turn["prompt_ts"] or datetime.now()
        response_ts = turn["response_ts"] or prompt_ts

        # Insert conversation_call
        await conn.execute(
            """
            INSERT INTO conversation_calls (call_id, session_id, model_name, provider, status, created_at, completed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (call_id) DO NOTHING
            """,
            call_id,
            session_id,
            model,
            "anthropic",
            "completed",
            prompt_ts,
            response_ts,
        )

        # Build request payload (what _extract_preview_message expects)
        messages = []
        if turn["prompt"]:
            messages.append({"role": "user", "content": turn["prompt"]})

        request_payload = {
            "final_request": {"messages": messages, "model": model},
            "original_request": {"messages": messages, "model": model},
            "final_model": model,
        }

        # Insert request event
        await conn.execute(
            """
            INSERT INTO conversation_events (id, call_id, session_id, event_type, payload, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            uuid.uuid4(),
            call_id,
            session_id,
            "transaction.request_recorded",
            json.dumps(request_payload),
            prompt_ts,
        )
        events_inserted += 1

        # Insert response event if we have a response
        if turn["response"]:
            response_payload = {
                "final_response": {
                    "choices": [{"message": {"role": "assistant", "content": turn["response"]}}]
                },
                "original_response": {
                    "choices": [{"message": {"role": "assistant", "content": turn["response"]}}]
                },
            }
            await conn.execute(
                """
                INSERT INTO conversation_events (id, call_id, session_id, event_type, payload, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid.uuid4(),
                call_id,
                session_id,
                "transaction.streaming_response_recorded",
                json.dumps(response_payload),
                response_ts,
            )
            events_inserted += 1

    print(f"  Imported {filepath.name}: {len(turns)} turns, {events_inserted} events as '{session_id}'")
    return events_inserted


async def main():
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/import_session_csvs.py /path/to/csv/dir [--dry-run]")
        print("\nExample:")
        print("  uv run python scripts/import_session_csvs.py ~/build/luthien-private-session-logs/")
        sys.exit(1)

    csv_dir = Path(sys.argv[1]).expanduser()
    dry_run = "--dry-run" in sys.argv

    if not csv_dir.exists():
        print(f"Error: Directory not found: {csv_dir}")
        sys.exit(1)

    # Find CSV files
    csv_files = sorted(csv_dir.glob("*.csv"))
    # Exclude template
    csv_files = [f for f in csv_files if "TEMPLATE" not in f.name]

    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        sys.exit(1)

    print(f"Found {len(csv_files)} CSV files in {csv_dir}")
    if dry_run:
        print("DRY RUN - no data will be written\n")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL not set in environment")
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        total_events = 0
        for csv_file in csv_files:
            events = await import_session(conn, csv_file, dry_run=dry_run)
            total_events += events

        print(f"\nTotal: {total_events} events {'would be' if dry_run else ''} imported")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
