"""Unit tests for the `maint_record_check` bash helper (`lib/config.sh`).

`maint_record_check` is bash, and the bug it regression-guards is a
bash-parsing bug (`${4:-{}}` appends a stray `}`), so these tests must
exercise the real shell function via subprocess — a pure-Python test of
the embedded snippet would not reproduce it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tests.luthien_proxy.unit_tests.automated_maintenance.conftest import (
    AUTOMATED_MAINTENANCE_LIB,
)

_CONFIG_SH = AUTOMATED_MAINTENANCE_LIB / "config.sh"


def _run_record_check(run_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Source config.sh and invoke maint_record_check with the given args.

    Seeds a valid results.json first, the way maint_start_run would.
    """
    (run_dir / "results.json").write_text('{"run_id":"t","checks":{},"autofix":null}\n')
    quoted = " ".join(f"'{a}'" for a in args)
    script = f'set -euo pipefail; source "{_CONFIG_SH}"; maint_record_check {quoted}'
    return subprocess.run(
        ["bash", "-c", script],
        env={"MAINT_RUN_DIR": str(run_dir), "HOME": str(run_dir), "PATH": _path()},
        capture_output=True,
        text=True,
    )


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")


def _checks(run_dir: Path) -> dict:
    return json.loads((run_dir / "results.json").read_text())["checks"]


def test_records_check_with_extra_json(tmp_path):
    """A check with an extra payload lands in results.json with parsed fields.

    Regression: `${4:-{}}` appended a stray `}` to the extra arg, so
    `json.loads(extra)` raised and the check was silently dropped, leaving
    `checks: {}`.
    """
    proc = _run_record_check(
        tmp_path, "dev_checks", "pass", "dev_checks.log", '{"duration_s":24,"exit_code":0}'
    )
    assert proc.returncode == 0, proc.stderr
    checks = _checks(tmp_path)
    assert "dev_checks" in checks
    assert checks["dev_checks"]["status"] == "pass"
    assert checks["dev_checks"]["log"] == "dev_checks.log"
    assert checks["dev_checks"]["duration_s"] == 24
    assert checks["dev_checks"]["exit_code"] == 0


def test_records_check_with_nested_brace_payload(tmp_path):
    """A richer extra payload (doc_drift-style, more braces) is parsed intact."""
    proc = _run_record_check(
        tmp_path,
        "doc_drift",
        "fail",
        "doc_drift.log",
        '{"duration_s":96,"exit_code":1,"report":"doc_drift.md"}',
    )
    assert proc.returncode == 0, proc.stderr
    checks = _checks(tmp_path)
    assert checks["doc_drift"]["report"] == "doc_drift.md"
    assert checks["doc_drift"]["exit_code"] == 1


def test_records_check_without_extra_defaults_to_empty_object(tmp_path):
    """Omitting the extra arg records the check with no extra fields (no crash)."""
    proc = _run_record_check(tmp_path, "e2e_sqlite", "pass", "e2e_sqlite.log")
    assert proc.returncode == 0, proc.stderr
    checks = _checks(tmp_path)
    assert checks["e2e_sqlite"]["status"] == "pass"
    # Only the two baseline keys; the default extra is an empty object.
    assert set(checks["e2e_sqlite"]) == {"status", "log"}


@pytest.mark.skipif(not _CONFIG_SH.exists(), reason="config.sh not present")
def test_config_sh_exists():
    assert _CONFIG_SH.is_file()
