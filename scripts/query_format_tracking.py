#!/usr/bin/env python3
"""Query and display format tracking events from database.

This script retrieves Luthien transaction tracking events to help debug
format conversion issues across the request/response pipeline.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from dotenv import load_dotenv


async def main():
    """Query and display format tracking events."""
    # Load environment variables
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment")
        sys.exit(1)

    # Replace docker hostname with localhost for local execution
    database_url = database_url.replace("@db:", "@localhost:")

    # Connect to database
    conn = await asyncpg.connect(database_url)

    try:
        # Query recent Luthien transaction events
        query = """
            SELECT call_id, event_type, data, created_at
            FROM conversation_events
            WHERE event_type LIKE 'luthien.%'
            ORDER BY created_at DESC
            LIMIT 50
        """

        rows = await conn.fetch(query)

        if not rows:
            print("No Luthien transaction events found in database")
            return

        # Group events by call_id
        events_by_call = {}
        for row in rows:
            call_id = row["call_id"]
            if call_id not in events_by_call:
                events_by_call[call_id] = []
            events_by_call[call_id].append(row)

        # Display events grouped by transaction
        print(f"\n{'=' * 80}")
        print(f"Found {len(rows)} Luthien events across {len(events_by_call)} transactions")
        print(f"{'=' * 80}\n")

        for call_id, events in events_by_call.items():
            print(f"\n{'─' * 80}")
            print(f"Transaction: {call_id}")
            print(f"{'─' * 80}")

            # Sort events by timestamp within this transaction
            events.sort(key=lambda e: e["created_at"])

            for event in events:
                event_type = event["event_type"]
                data = event["data"]
                timestamp = event["created_at"]

                print(f"\n[{timestamp}] {event_type}")

                # Pretty print based on event type
                if event_type == "luthien.request.incoming":
                    endpoint = data.get("endpoint", "unknown")
                    format_type = data.get("format", "unknown")
                    messages = data.get("body", {}).get("messages", [])
                    print(f"  Endpoint: {endpoint}")
                    print(f"  Format: {format_type}")
                    print(f"  Messages: {len(messages)}")
                    for i, msg in enumerate(messages):
                        role = msg.get("role", "unknown")
                        content = msg.get("content", [])
                        if isinstance(content, list):
                            content_types = [c.get("type") for c in content if isinstance(c, dict)]
                            print(f"    Message {i}: role={role}, content_types={content_types}")
                        else:
                            print(f"    Message {i}: role={role}, content={str(content)[:100]}")

                elif event_type == "luthien.request.format_conversion":
                    conversion = data.get("conversion", "unknown")
                    messages = data.get("result", {}).get("messages", [])
                    print(f"  Conversion: {conversion}")
                    print(f"  Result Messages: {len(messages)}")
                    for i, msg in enumerate(messages):
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        print(f"    Message {i}: role={role}, content={str(content)[:100]}")

                elif event_type == "luthien.backend.request":
                    request_data = data.get("request", {})
                    messages = request_data.get("messages", [])
                    print(f"  Backend Request Messages: {len(messages)}")
                    for i, msg in enumerate(messages):
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        print(f"    Message {i}: role={role}, content={str(content)[:100]}")

                else:
                    # For other events, just dump the data
                    print(f"  Data: {json.dumps(data, indent=4, default=str)[:500]}")

            print(f"\n{'─' * 80}\n")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
