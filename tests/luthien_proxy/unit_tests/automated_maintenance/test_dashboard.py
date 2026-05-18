"""Unit tests for the maintenance dashboard renderer (`scripts/automated_maintenance/lib/dashboard.py`)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from tests.luthien_proxy.unit_tests.automated_maintenance.conftest import (
    AUTOMATED_MAINTENANCE_LIB,
)

# The dashboard lives outside the package tree, so import it by file path.
_DASHBOARD_PATH = AUTOMATED_MAINTENANCE_LIB / "dashboard.py"
_spec = importlib.util.spec_from_file_location("maint_dashboard", _DASHBOARD_PATH)
assert _spec is not None and _spec.loader is not None
dashboard = importlib.util.module_from_spec(_spec)
sys.modules["maint_dashboard"] = dashboard
_spec.loader.exec_module(dashboard)


def _write_run(runs_dir: Path, run_id: str, **overrides) -> Path:
    """Create a minimal results.json under runs_dir/run_id/."""
    run = runs_dir / run_id
    run.mkdir(parents=True)
    data = {
        "run_id": run_id,
        "started_at": "2026-05-09T08:00:00Z",
        "finished_at": "2026-05-09T08:01:00Z",
        "host": "test",
        "checks": {},
        "autofix": None,
        **overrides,
    }
    (run / "results.json").write_text(json.dumps(data))
    return run


def test_load_runs_returns_sorted_descending(tmp_path):
    _write_run(tmp_path, "2026-05-09-080000")
    _write_run(tmp_path, "2026-05-10-080000")
    _write_run(tmp_path, "2026-05-08-080000")
    runs = dashboard.load_runs(tmp_path)
    ids = [r["run_id"] for r in runs]
    assert ids == ["2026-05-10-080000", "2026-05-09-080000", "2026-05-08-080000"]


def test_load_runs_skips_malformed_json(tmp_path):
    good = _write_run(tmp_path, "2026-05-09-080000")
    bad = tmp_path / "2026-05-09-090000"
    bad.mkdir()
    (bad / "results.json").write_text("{not json")
    runs = dashboard.load_runs(tmp_path)
    assert [r["run_id"] for r in runs] == ["2026-05-09-080000"]
    assert runs[0]["_dir"] == good


def test_load_runs_skips_directories_without_results(tmp_path):
    _write_run(tmp_path, "2026-05-09-080000")
    (tmp_path / "scratch").mkdir()
    runs = dashboard.load_runs(tmp_path)
    assert len(runs) == 1


def test_load_runs_on_missing_dir_returns_empty(tmp_path):
    assert dashboard.load_runs(tmp_path / "nope") == []


def test_render_index_handles_empty_runs():
    html_out = dashboard.render_index([])
    assert "No runs yet." in html_out
    assert "luthien-proxy maintenance" in html_out


def test_render_index_includes_run_pills_and_links(tmp_path):
    _write_run(
        tmp_path,
        "2026-05-09-080000",
        overall="fail",
        checks={"doc_drift": {"status": "fail", "log": "d.log", "duration_s": 5, "exit_code": 1}},
        autofix={"status": "opened_pr", "pr_url": "https://example.com/pr/1"},
    )
    runs = dashboard.load_runs(tmp_path)
    html_out = dashboard.render_index(runs)
    assert "2026-05-09-080000" in html_out
    assert "fail" in html_out
    assert 'href="runs/2026-05-09-080000.html"' in html_out
    assert "https://example.com/pr/1" in html_out
    assert "opened_pr" in html_out


def test_render_run_uses_report_field_for_link(tmp_path):
    run = _write_run(
        tmp_path,
        "2026-05-09-080000",
        checks={
            "doc_drift": {
                "status": "fail",
                "log": "d.log",
                "duration_s": 5,
                "exit_code": 1,
                "report": "my_custom_report.md",
            }
        },
    )
    (run / "d.log").write_text("log contents")
    (run / "my_custom_report.md").write_text("CUSTOM REPORT CONTENTS")
    runs = dashboard.load_runs(tmp_path)
    html_out = dashboard.render_run(runs[0])
    assert "CUSTOM REPORT CONTENTS" in html_out
    assert "log contents" in html_out


def test_render_run_missing_report_falls_through(tmp_path):
    """If the JSON ``report`` field is absent, no report block renders."""
    _write_run(
        tmp_path,
        "2026-05-09-080000",
        checks={"dev_checks": {"status": "pass", "log": "d.log", "duration_s": 5, "exit_code": 0}},
    )
    runs = dashboard.load_runs(tmp_path)
    html_out = dashboard.render_run(runs[0])
    assert "summary>Report" not in html_out


def test_prune_keeps_most_recent_n(tmp_path):
    for i in range(5):
        _write_run(tmp_path, f"2026-05-{1 + i:02d}-080000")
    dashboard.prune(tmp_path, retention=3)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [
        "2026-05-03-080000",
        "2026-05-04-080000",
        "2026-05-05-080000",
    ]


def test_prune_retention_zero_is_noop(tmp_path):
    for i in range(3):
        _write_run(tmp_path, f"2026-05-{1 + i:02d}-080000")
    dashboard.prune(tmp_path, retention=0)
    assert sum(1 for _ in tmp_path.iterdir()) == 3


def test_pill_escapes_status():
    out = dashboard.pill("fail")
    assert "fail" in out
    assert 'class="pill"' in out


def test_render_index_escapes_malicious_check_names(tmp_path):
    """Check names from results.json flow into title="..." attributes —
    confirm injected HTML is escaped.
    """
    _write_run(
        tmp_path,
        "2026-05-09-080000",
        checks={
            '"><script>alert(1)</script>': {
                "status": "pass",
                "log": "x.log",
                "duration_s": 1,
                "exit_code": 0,
            }
        },
    )
    runs = dashboard.load_runs(tmp_path)
    html_out = dashboard.render_index(runs)
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_render_run_escapes_malicious_check_names(tmp_path):
    run = _write_run(
        tmp_path,
        "2026-05-09-080000",
        checks={
            "<img src=x onerror=alert(1)>": {
                "status": "pass",
                "log": "x.log",
                "duration_s": 1,
                "exit_code": 0,
            }
        },
    )
    (run / "x.log").write_text("ok")
    runs = dashboard.load_runs(tmp_path)
    html_out = dashboard.render_run(runs[0])
    assert "<img src=x" not in html_out
    assert "&lt;img" in html_out


def test_fmt_dt_handles_missing_and_invalid():
    assert dashboard.fmt_dt(None) == "—"
    assert dashboard.fmt_dt("not-a-date") == "not-a-date"
    assert "2026-05-09" in dashboard.fmt_dt("2026-05-09T08:00:00Z")


@pytest.mark.timeout(5)
def test_main_prunes_orphan_per_run_pages(tmp_path, monkeypatch):
    """`prune` removes old run dirs from disk; `main` must also delete
    the corresponding `public_dir/runs/<id>.html` files so they don't
    accumulate forever.
    """
    runs_dir = tmp_path / "runs"
    public_dir = tmp_path / "public"
    (public_dir / "runs").mkdir(parents=True)
    # Seed an orphan page that has no backing run dir.
    orphan = public_dir / "runs" / "2024-01-01-080000.html"
    orphan.write_text("<html>old</html>")
    # And a current run.
    run = _write_run(runs_dir, "2026-05-09-080000")
    (run / "x.log").write_text("ok")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dashboard.py",
            "--runs-dir",
            str(runs_dir),
            "--public-dir",
            str(public_dir),
            "--retention",
            "10",
        ],
    )
    dashboard.main()

    assert not orphan.exists(), "orphan per-run page should be swept"
    assert (public_dir / "runs" / "2026-05-09-080000.html").exists()


@pytest.mark.timeout(5)
def test_main_writes_index_and_per_run_pages(tmp_path, monkeypatch):
    runs_dir = tmp_path / "runs"
    public_dir = tmp_path / "public"
    run = _write_run(
        runs_dir,
        "2026-05-09-080000",
        checks={"dev_checks": {"status": "pass", "log": "d.log", "duration_s": 5, "exit_code": 0}},
    )
    (run / "d.log").write_text("ok")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dashboard.py",
            "--runs-dir",
            str(runs_dir),
            "--public-dir",
            str(public_dir),
            "--retention",
            "10",
        ],
    )
    dashboard.main()
    assert (public_dir / "index.html").exists()
    assert (public_dir / "runs" / "2026-05-09-080000.html").exists()
