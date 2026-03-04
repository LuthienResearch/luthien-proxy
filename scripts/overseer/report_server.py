"""Live-updating HTML dashboard for overseer sessions via SSE.

Serves a self-contained dark-themed dashboard at / and pushes state updates
to connected browsers via Server-Sent Events at /events. The ReportServer
accumulates turn summaries and anomalies, broadcasting each change to all
connected SSE clients.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from aiohttp import web
from scripts.overseer.stream_parser import TurnSummary


class ReportServer:
    """Accumulates turn data and serves a live-updating dashboard via SSE."""

    def __init__(self, port: int = 8080) -> None:
        self.port = port
        self.turns: list[TurnSummary] = []
        self.all_anomalies: list[dict] = []
        self.total_cost: float = 0.0
        self.status: str = "starting"
        self.task: str = ""
        self._sse_queues: list[asyncio.Queue[str]] = []
        self._runner: web.AppRunner | None = None

    def add_turn(self, summary: TurnSummary, overseer_analysis: str = "") -> None:
        """Append a turn summary, accumulate cost, extract anomalies, broadcast."""
        self.turns.append(summary)
        self.total_cost += summary.cost_usd
        for anomaly in summary.anomalies:
            self.all_anomalies.append({"turn": summary.turn_number, "source": "rule", "message": anomaly})
        self._broadcast(self.state_as_json())

    def add_llm_anomalies(self, turn_number: int, anomalies: list[str]) -> None:
        """Add anomalies detected by the overseer LLM and broadcast if any."""
        for anomaly in anomalies:
            self.all_anomalies.append({"turn": turn_number, "source": "llm", "message": anomaly})
        if anomalies:
            self._broadcast(self.state_as_json())

    def set_status(self, status: str) -> None:
        """Update the session status and broadcast."""
        self.status = status
        self._broadcast(self.state_as_json())

    def state_as_json(self) -> str:
        """Serialize current state for SSE clients."""
        tool_counts: Counter[str] = Counter()
        for turn in self.turns:
            for tool in turn.tools_used:
                tool_counts[tool] += 1

        return json.dumps(
            {
                "status": self.status,
                "task": self.task,
                "turn_count": len(self.turns),
                "total_cost": round(self.total_cost, 4),
                "anomaly_count": len(self.all_anomalies),
                "anomalies": self.all_anomalies[-20:],
                "tool_counts": dict(tool_counts),
                "turns": [
                    {
                        "number": t.turn_number,
                        "success": t.is_success,
                        "tools": t.tools_used,
                        "cost": round(t.cost_usd, 4),
                        "duration": round(t.duration_seconds, 1),
                        "anomalies": t.anomalies,
                        "result_preview": t.result_text[:150],
                    }
                    for t in self.turns
                ],
            }
        )

    def _broadcast(self, data: str) -> None:
        """Push data to all connected SSE client queues."""
        for queue in self._sse_queues:
            queue.put_nowait(data)

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=build_dashboard_html(), content_type="text/html")

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        await response.prepare(request)

        queue: asyncio.Queue[str] = asyncio.Queue()
        self._sse_queues.append(queue)

        await response.write(f"data: {self.state_as_json()}\n\n".encode())

        try:
            while True:
                data = await queue.get()
                await response.write(f"data: {data}\n\n".encode())
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self._sse_queues.remove(queue)
        return response

    async def start(self) -> None:
        """Create the aiohttp app and start the HTTP server."""
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/events", self._handle_sse)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

    async def stop(self) -> None:
        """Clean up the HTTP server."""
        if self._runner:
            await self._runner.cleanup()


def build_dashboard_html() -> str:
    """Generate a self-contained HTML dashboard with SSE client.

    Dark theme with live-updating metrics, anomaly list, and turn history.
    """
    return """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Overseer Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    padding: 1.5rem;
    line-height: 1.5;
  }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  h2 { font-size: 1.1rem; color: #94a3b8; margin: 1.5rem 0 0.75rem; }
  .status {
    font-size: 1.1rem;
    padding: 0.4rem 0.8rem;
    border-radius: 6px;
    display: inline-block;
    margin-bottom: 1rem;
  }
  .status-running { background: #164e63; color: #67e8f9; }
  .status-finished { background: #14532d; color: #86efac; }
  .status-error { background: #7f1d1d; color: #fca5a5; }
  .status-starting { background: #1e293b; color: #94a3b8; }
  .task { color: #94a3b8; font-size: 0.9rem; margin-bottom: 1rem; }
  .metrics {
    display: flex;
    gap: 1rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  .metric {
    background: #1e293b;
    padding: 1rem 1.25rem;
    border-radius: 8px;
    min-width: 130px;
  }
  .metric-value { font-size: 1.75rem; font-weight: bold; color: #38bdf8; }
  .metric-label { font-size: 0.8rem; color: #94a3b8; text-transform: uppercase; }
  .anomaly {
    background: #1c1305;
    border-left: 3px solid #f97316;
    padding: 0.5rem 1rem;
    margin: 0.25rem 0;
    border-radius: 0 4px 4px 0;
    font-size: 0.9rem;
  }
  .anomaly-source {
    font-size: 0.75rem;
    color: #f97316;
    text-transform: uppercase;
    margin-right: 0.5rem;
  }
  .turn {
    background: #1e293b;
    padding: 0.75rem 1rem;
    margin: 0.25rem 0;
    border-radius: 4px;
    font-size: 0.9rem;
  }
  .turn.ok { border-left: 3px solid #22c55e; }
  .turn.error { border-left: 3px solid #ef4444; }
  .turn-header { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
  .turn-number { font-weight: bold; }
  .turn-tools { color: #94a3b8; }
  .turn-cost { color: #38bdf8; }
  .turn-duration { color: #a78bfa; }
  .turn-anomaly-badge { color: #f97316; font-size: 0.85rem; }
  .turn-preview { color: #64748b; font-size: 0.8rem; margin-top: 0.25rem; }
  .empty { color: #475569; font-style: italic; }
  .tool-counts { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .tool-badge {
    background: #1e293b;
    padding: 0.25rem 0.75rem;
    border-radius: 12px;
    font-size: 0.8rem;
  }
  .tool-badge-count { color: #38bdf8; font-weight: bold; }
</style>
</head>
<body>
<h1>Overseer Dashboard</h1>
<div class="task" id="task"></div>
<div class="status status-starting" id="status">Connecting...</div>

<div class="metrics">
  <div class="metric">
    <div class="metric-value" id="turns">0</div>
    <div class="metric-label">Turns</div>
  </div>
  <div class="metric">
    <div class="metric-value" id="cost">$0.00</div>
    <div class="metric-label">Cost</div>
  </div>
  <div class="metric">
    <div class="metric-value" id="anomaly-count">0</div>
    <div class="metric-label">Anomalies</div>
  </div>
</div>

<h2>Tool Usage</h2>
<div class="tool-counts" id="tool-counts">
  <span class="empty">No tools used yet</span>
</div>

<h2>Anomalies</h2>
<div id="anomaly-list">
  <div class="empty">No anomalies detected</div>
</div>

<h2>Turns</h2>
<div id="turn-list">
  <div class="empty">Waiting for first turn...</div>
</div>

<script>
function esc(s) {
  var d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

var es = new EventSource("/events");

es.onmessage = function(e) {
  var d = JSON.parse(e.data);

  var statusEl = document.getElementById("status");
  statusEl.textContent = d.status;
  statusEl.className = "status status-" + d.status.split(":")[0].trim();

  document.getElementById("task").textContent = d.task ? "Task: " + d.task : "";
  document.getElementById("turns").textContent = d.turn_count;
  document.getElementById("cost").textContent = "$" + d.total_cost.toFixed(2);
  document.getElementById("anomaly-count").textContent = d.anomaly_count;

  var tcEl = document.getElementById("tool-counts");
  var tools = Object.keys(d.tool_counts);
  if (tools.length === 0) {
    tcEl.innerHTML = '<span class="empty">No tools used yet</span>';
  } else {
    tcEl.innerHTML = tools.map(function(name) {
      return '<span class="tool-badge">' + name +
        ' <span class="tool-badge-count">' + d.tool_counts[name] + '</span></span>';
    }).join("");
  }

  var alEl = document.getElementById("anomaly-list");
  if (d.anomalies.length === 0) {
    alEl.innerHTML = '<div class="empty">No anomalies detected</div>';
  } else {
    alEl.innerHTML = d.anomalies.map(function(a) {
      return '<div class="anomaly">' +
        '<span class="anomaly-source">' + a.source + '</span>' +
        'Turn ' + a.turn + ': ' + esc(a.message) + '</div>';
    }).join("");
  }

  var tlEl = document.getElementById("turn-list");
  if (d.turns.length === 0) {
    tlEl.innerHTML = '<div class="empty">Waiting for first turn...</div>';
  } else {
    tlEl.innerHTML = d.turns.slice().reverse().map(function(t) {
      var cls = t.success ? "ok" : "error";
      var anomalyBadge = t.anomalies.length > 0
        ? ' <span class="turn-anomaly-badge">' + t.anomalies.length + ' anomalies</span>'
        : "";
      return '<div class="turn ' + cls + '">' +
        '<div class="turn-header">' +
        '<span class="turn-number">Turn ' + t.number + '</span>' +
        '<span class="turn-tools">' + (t.tools.join(", ") || "no tools") + '</span>' +
        '<span class="turn-duration">' + t.duration + 's</span>' +
        '<span class="turn-cost">$' + t.cost.toFixed(4) + '</span>' +
        anomalyBadge +
        '</div>' +
        '<div class="turn-preview">' + esc(t.result_preview) + '</div>' +
        '</div>';
    }).join("");
  }
};

es.onerror = function() {
  document.getElementById("status").textContent = "Disconnected";
  document.getElementById("status").className = "status status-error";
};
</script>
</body>
</html>"""
