"""Seed the SQLite database used for the harmful baseline demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from demo_lib import seed_demo_database, verify_demo_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo SQLite database")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("dev/demo_baseline.sqlite"),
        help="Path where the SQLite database should be written.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Fail if the database already exists instead of overwriting it.",
    )
    args = parser.parse_args()

    target = args.path
    overwrite = not args.no_overwrite
    if target.exists() and not overwrite:
        raise SystemExit(f"{target} already exists; rerun without --no-overwrite to replace it")

    db_path = seed_demo_database(target, overwrite=overwrite)
    report = verify_demo_database(db_path)
    print(report.to_pretty_text())


if __name__ == "__main__":
    main()
