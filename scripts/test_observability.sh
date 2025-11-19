#!/bin/bash
# Test observability records end-to-end

set -e

echo "=== Testing Luthien Observability Records ==="
echo ""

# 1. Rebuild gateway with new code
echo "📦 Rebuilding gateway with latest code..."
docker compose build gateway

# 2. Restart gateway
echo "🔄 Restarting gateway..."
docker compose up -d gateway

# Wait for gateway to be healthy
echo "⏳ Waiting for gateway to be healthy..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Gateway is healthy"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ Gateway failed to become healthy"
        exit 1
    fi
    sleep 1
done

# 3. Make a test request
echo ""
echo "🔧 Making test request to /v1/chat/completions..."
CALL_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
echo "   Call ID: $CALL_ID"

curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d "{
    \"model\": \"gpt-3.5-turbo\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Say hello in 3 words\"}],
    \"max_tokens\": 10
  }" | python3 -m json.tool | head -20

echo ""
echo "✅ Request completed"

# 4. Wait a moment for logs to be indexed
echo ""
echo "⏳ Waiting for logs to be indexed in Loki..."
sleep 3

# 5. Query Loki for our observability records
echo ""
echo "📊 Querying Loki for observability records..."
echo ""

# Query for all luthien.payload events in the last 5 minutes
LOKI_QUERY='query={service_name="luthien-gateway"} |= "luthien.payload" | json'
LOKI_URL="http://localhost:3100/loki/api/v1/query_range"
START_TIME=$(date -u -v-5M +%s)000000000
END_TIME=$(date -u +%s)000000000

curl -sG "$LOKI_URL" \
  --data-urlencode "query=$LOKI_QUERY" \
  --data-urlencode "start=$START_TIME" \
  --data-urlencode "end=$END_TIME" \
  --data-urlencode "limit=100" \
  | python3 -c '
import json
import sys

data = json.load(sys.stdin)
if data.get("status") != "success":
    print("❌ Query failed:", data)
    sys.exit(1)

results = data.get("data", {}).get("result", [])
if not results:
    print("⚠️  No results found. Records may not have been indexed yet.")
    sys.exit(0)

print(f"✅ Found {len(results)} log streams with observability records")
print("")

# Extract and display unique stages
stages_seen = set()
for stream in results:
    for entry in stream.get("values", []):
        timestamp_ns, log_line = entry
        try:
            log_data = json.loads(log_line)
            message = log_data.get("message", "")

            # Parse the JSON message if it contains stage info
            if "stage" in message or "luthien.payload" in log_line:
                # Try to extract stage from the logged data
                if "stage" in log_data:
                    stages_seen.add(log_data["stage"])
        except:
            pass

if stages_seen:
    print("📋 Observed stages:")
    for stage in sorted(stages_seen):
        print(f"   - {stage}")
else:
    print("📋 Showing recent luthien.payload events:")
    for stream in results[:3]:
        for entry in stream.get("values", [])[:2]:
            timestamp_ns, log_line = entry
            try:
                log_data = json.loads(log_line)
                ts = log_data.get("timestamp", "")
                msg = log_data.get("message", "")[:100]
                print(f"   {ts} - {msg}")
            except:
                print(f"   {log_line[:100]}")
'

echo ""
echo "=== Test Complete ==="
echo ""
echo "🌐 View full logs in Grafana: http://localhost:3000"
echo "   Query: {service_name=\"luthien-gateway\"} | json | stage != \"\""
