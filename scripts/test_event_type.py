#!/usr/bin/env python3
"""Test querying by record_type (LuthienRecord types)."""

from datetime import datetime, timedelta

import requests

loki_url = "http://localhost:3100/loki/api/v1/query_range"

now = datetime.now()
start = now - timedelta(minutes=10)

params = {
    "start": str(int(start.timestamp() * 1e9)),
    "end": str(int(now.timestamp() * 1e9)),
    "limit": 5,
}

queries = [
    ("All PipelineRecord events", '{app="luthien-gateway", record_type="pipeline"}'),
    (
        "Pipeline records - client_request",
        '{app="luthien-gateway", record_type="pipeline", pipeline_stage="client_request"}',
    ),
    (
        "Pipeline records - backend_request",
        '{app="luthien-gateway", record_type="pipeline", pipeline_stage="backend_request"}',
    ),
    (
        "Pipeline records - client_response",
        '{app="luthien-gateway", record_type="pipeline", pipeline_stage="client_response"}',
    ),
]

for name, query in queries:
    print(f"\n{'=' * 70}")
    print(f"Query: {name}")
    print(f"LogQL: {query}")
    print("=" * 70)

    params["query"] = query
    resp = requests.get(loki_url, params=params, timeout=10)
    data = resp.json()

    if data.get("status") != "success":
        print(f"❌ Failed: {data}")
        continue

    results = data.get("data", {}).get("result", [])

    if not results:
        print("⚠️  No results")
        continue

    print(f"✅ Found {len(results)} streams")
    total_entries = sum(len(stream.get("values", [])) for stream in results)
    print(f"   Total entries: {total_entries}")

    # Show unique stages if available
    stages = set()
    for stream in results:
        if "stage" in stream.get("stream", {}):
            stages.add(stream["stream"]["stage"])

    if stages:
        print(f"   Stages: {', '.join(sorted(stages))}")
