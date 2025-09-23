"""Check integrity of the harmful baseline SQLite database."""

from __future__ import annotations

import argparse
from pathlib import Path

from demo_lib import verify_demo_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the demo SQLite database")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("dev/demo_baseline.sqlite"),
        help="Path to the SQLite database to verify.",
    )
    args = parser.parse_args()

    target = args.path
    if not target.exists():
        raise SystemExit(f"{target} does not exist; run scripts/demo_seed_db.py first")

    report = verify_demo_database(target)
    print(report.to_pretty_text())


if __name__ == "__main__":
    main()
