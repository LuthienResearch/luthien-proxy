"""Tests for the overseer report server module."""

import json

from scripts.overseer.report_server import ReportServer, build_dashboard_html
from scripts.overseer.stream_parser import TurnSummary


def _make_summary(
    turn_number: int = 1,
    is_success: bool = True,
    tools_used: list[str] | None = None,
    cost_usd: float = 0.01,
    duration_seconds: float = 3.0,
    anomalies: list[str] | None = None,
) -> TurnSummary:
    return TurnSummary(
        turn_number=turn_number,
        session_id="s1",
        is_success=is_success,
        tools_used=tools_used or ["Read"],
        tool_call_count=len(tools_used or ["Read"]),
        tool_result_count=len(tools_used or ["Read"]),
        cost_usd=cost_usd,
        duration_seconds=duration_seconds,
        result_text="Done.",
        anomalies=anomalies or [],
        num_turns_reported=1,
    )


class TestBuildDashboardHtml:
    def test_renders_valid_html(self):
        html = build_dashboard_html()
        assert "<html" in html
        assert "EventSource" in html
        assert "dashboard" in html.lower() or "overseer" in html.lower()

    def test_has_sse_endpoint(self):
        html = build_dashboard_html()
        assert "/events" in html

    def test_has_dark_theme(self):
        html = build_dashboard_html()
        assert "#0f172a" in html
        assert "#e2e8f0" in html

    def test_has_system_ui_font(self):
        html = build_dashboard_html()
        assert "system-ui" in html


class TestReportServerAddTurn:
    def test_add_successful_turn(self):
        server = ReportServer(port=0)
        summary = _make_summary(cost_usd=0.01)
        server.add_turn(summary)

        assert len(server.turns) == 1
        assert server.total_cost == 0.01

    def test_add_multiple_turns_accumulates_cost(self):
        server = ReportServer(port=0)
        server.add_turn(_make_summary(turn_number=1, cost_usd=0.01))
        server.add_turn(_make_summary(turn_number=2, cost_usd=0.02))

        assert len(server.turns) == 2
        assert server.total_cost == 0.03

    def test_add_turn_with_anomaly(self):
        server = ReportServer(port=0)
        summary = _make_summary(
            is_success=False,
            anomalies=["Slow turn: 120.0s"],
        )
        server.add_turn(summary)

        assert len(server.all_anomalies) == 1
        assert server.all_anomalies[0]["message"] == "Slow turn: 120.0s"
        assert server.all_anomalies[0]["source"] == "rule"
        assert server.all_anomalies[0]["turn"] == 1

    def test_add_turn_with_multiple_anomalies(self):
        server = ReportServer(port=0)
        summary = _make_summary(
            anomalies=["Slow turn: 120.0s", "Error result: connection refused"],
        )
        server.add_turn(summary)
        assert len(server.all_anomalies) == 2


class TestReportServerAddLlmAnomalies:
    def test_add_llm_anomalies(self):
        server = ReportServer(port=0)
        server.add_llm_anomalies(1, ["Response truncated"])

        assert len(server.all_anomalies) == 1
        assert server.all_anomalies[0]["source"] == "llm"
        assert server.all_anomalies[0]["turn"] == 1

    def test_empty_anomalies_does_not_add(self):
        server = ReportServer(port=0)
        server.add_llm_anomalies(1, [])
        assert len(server.all_anomalies) == 0


class TestReportServerSetStatus:
    def test_set_status(self):
        server = ReportServer(port=0)
        assert server.status == "starting"
        server.set_status("running")
        assert server.status == "running"


class TestReportServerStateAsJson:
    def test_has_expected_keys(self):
        server = ReportServer(port=0)
        state = json.loads(server.state_as_json())

        assert "status" in state
        assert "task" in state
        assert "turn_count" in state
        assert "total_cost" in state
        assert "anomaly_count" in state
        assert "anomalies" in state
        assert "tool_counts" in state
        assert "turns" in state

    def test_reflects_added_turns(self):
        server = ReportServer(port=0)
        server.add_turn(_make_summary(tools_used=["Read", "Bash"], cost_usd=0.05))

        state = json.loads(server.state_as_json())
        assert state["turn_count"] == 1
        assert state["total_cost"] == 0.05
        assert state["tool_counts"]["Read"] == 1
        assert state["tool_counts"]["Bash"] == 1

    def test_anomalies_capped_at_20(self):
        server = ReportServer(port=0)
        for i in range(25):
            server.add_llm_anomalies(i, [f"anomaly-{i}"])

        state = json.loads(server.state_as_json())
        assert len(state["anomalies"]) == 20
        assert state["anomaly_count"] == 25

    def test_turn_summaries_in_json(self):
        server = ReportServer(port=0)
        server.add_turn(_make_summary(turn_number=1, cost_usd=0.01))

        state = json.loads(server.state_as_json())
        assert len(state["turns"]) == 1
        turn = state["turns"][0]
        assert turn["number"] == 1
        assert turn["success"] is True
        assert turn["cost"] == 0.01


class TestReportServerBroadcast:
    def test_broadcast_pushes_to_queues(self):
        """Verify _broadcast puts data into all connected SSE queues."""
        import asyncio

        server = ReportServer(port=0)
        q1: asyncio.Queue[str] = asyncio.Queue()
        q2: asyncio.Queue[str] = asyncio.Queue()
        server._sse_queues.extend([q1, q2])

        server._broadcast("test-data")

        assert q1.get_nowait() == "test-data"
        assert q2.get_nowait() == "test-data"
