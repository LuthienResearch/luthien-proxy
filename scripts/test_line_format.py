#!/usr/bin/env python3
"""Test LogQL line_format queries."""

from datetime import datetime, timedelta

import requests

loki_url = "http://localhost:3100/loki/api/v1/query_range"

now = datetime.now()
start = now - timedelta(minutes=10)

params = {
    "start": str(int(start.timestamp() * 1e9)),
    "end": str(int(now.timestamp() * 1e9)),
    "limit": 1,
}

# Test query with line_format
query = '{app="luthien-gateway", record_type="pipeline", payload_type="client_request"} | json | line_format "transaction_id: {{.transaction_id}}\\npayload: {{.payload}}"'

print("Testing LogQL with line_format:")
print(f"Query: {query}")
print("=" * 70)

params["query"] = query
resp = requests.get(loki_url, params=params, timeout=10)
data = resp.json()

if data.get("status") != "success":
    print(f"❌ Failed: {data}")
    exit(1)

results = data.get("data", {}).get("result", [])

if not results:
    print("⚠️  No results")
    exit(0)

# Get the first log entry
stream = results[0]
values = stream.get("values", [])

if not values:
    print("⚠️  No log entries in stream")
    exit(0)

# values[0] is [timestamp_ns, formatted_line]
timestamp_ns, formatted_line = values[0]

print("\nFormatted output from line_format:")
print(formatted_line)
print("\n" + "=" * 70)

# Also test without line_format to show transaction_id is there
print("\nNow testing without line_format (raw JSON):")
query_raw = '{app="luthien-gateway", record_type="pipeline", payload_type="client_request"}'
params["query"] = query_raw
resp = requests.get(loki_url, params=params, timeout=10)
data = resp.json()

if data.get("status") == "success":
    results = data.get("data", {}).get("result", [])
    if results and results[0].get("values"):
        raw_line = results[0]["values"][0][1]
        print(raw_line[:200] + "...")
