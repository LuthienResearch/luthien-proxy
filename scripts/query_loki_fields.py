#!/usr/bin/env python3
"""Query Loki to verify transaction_id and payload fields are extracted."""

import json
from datetime import datetime, timedelta

import requests

loki_url = "http://localhost:3100/loki/api/v1/query_range"

now = datetime.now()
start = now - timedelta(minutes=10)

params = {
    "query": '{app="luthien-gateway", record_type="pipeline", payload_type="client_request"}',
    "start": str(int(start.timestamp() * 1e9)),
    "end": str(int(now.timestamp() * 1e9)),
    "limit": 1,
}

print("Querying Loki for recent PipelineRecord...")
print(f"Query: {params['query']}")
print("=" * 70)

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

# values[0] is [timestamp_ns, log_line]
timestamp_ns, log_line = values[0]

print("\nRaw log line:")
print(log_line)
print("\n" + "=" * 70)

# Parse the JSON log line
try:
    log_json = json.loads(log_line)
    print("\nParsed JSON fields:")
    print(f"  transaction_id: {log_json.get('transaction_id', 'NOT FOUND')}")
    print(f"  payload (first 100 chars): {log_json.get('payload', 'NOT FOUND')[:100]}")
    print(f"  record_type: {log_json.get('record_type', 'NOT FOUND')}")
    print(f"  payload_type: {log_json.get('payload_type', 'NOT FOUND')}")
    print("\nFull payload:")
    print(log_json.get("payload", "NOT FOUND"))
except json.JSONDecodeError:
    print("❌ Failed to parse log line as JSON")
