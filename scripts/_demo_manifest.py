#!/usr/bin/env python3
"""Demo-manifest helper used by scripts/demo_{setup,toggle,reset}.sh.

Loads dev/demo/<name>/demo.toml and emits the bits the shell harness needs:
the demo dir, the template dir, the policy-set payload for a given
state+surface, and the list of available demos.

The harness is generic; this script encapsulates everything demo-specific so
adding a new demo doesn't require touching shell code.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR = REPO_ROOT / "dev" / "demo"

NOOP_POLICY = "luthien_proxy.policies.noop_policy:NoOpPolicy"
MULTI_SERIAL_POLICY = "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy"


def _die(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _load(demo: str) -> dict[str, Any]:
    path = DEMOS_DIR / demo / "demo.toml"
    if not path.exists():
        available = sorted(p.name for p in DEMOS_DIR.iterdir() if (p / "demo.toml").exists())
        _die(f"demo '{demo}' not found at {path}. Available: {available}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _fabricator(manifest: dict[str, Any], surface: str) -> dict[str, Any]:
    surfaces = manifest.get("surfaces", {})
    if surface not in surfaces:
        _die(f"demo '{manifest['name']}' does not declare surface '{surface}' (declared: {sorted(surfaces.keys())})")
    fab = surfaces[surface].get("fabricator")
    if not fab or "class" not in fab:
        _die(f"surface '{surface}' is missing a [surfaces.{surface}.fabricator] section with `class`.")
    return fab


def _payload(manifest: dict[str, Any], state: str, surface: str) -> dict[str, Any]:
    if state == "off":
        return {"policy_class_ref": NOOP_POLICY, "config": {}}

    fab = _fabricator(manifest, surface)
    fab_entry = {"class": fab["class"], "config": fab.get("config", {})}

    if state == "fail":
        return {"policy_class_ref": fab["class"], "config": fab.get("config", {})}

    if state == "succeed":
        protector = manifest.get("protector")
        if not protector or "class" not in protector:
            _die(f"demo '{manifest['name']}' is missing a [protector] section with `class`.")
        prot_entry = {"class": protector["class"], "config": protector.get("config", {})}
        return {
            "policy_class_ref": MULTI_SERIAL_POLICY,
            "config": {"policies": [fab_entry, prot_entry]},
        }

    _die(f"unknown state '{state}' (expected succeed|fail|off)")


def _list_demos() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for entry in sorted(DEMOS_DIR.iterdir()):
        manifest_path = entry / "demo.toml"
        if not manifest_path.exists():
            continue
        with manifest_path.open("rb") as fh:
            m = tomllib.load(fh)
        out.append((m.get("name", entry.name), m.get("short_description", "")))
    return out


def main(argv: list[str]) -> None:
    if not argv:
        _die("usage: _demo_manifest.py <list|demo-dir|template-dir|surfaces|policy> ...")
    cmd, *rest = argv

    if cmd == "list":
        for name, desc in _list_demos():
            print(f"{name}\t{desc}")
        return

    if not rest:
        _die(f"usage: _demo_manifest.py {cmd} <demo>")
    demo = rest[0]
    manifest = _load(demo)

    if cmd == "demo-dir":
        print(os.path.expanduser(manifest.get("demo_dir") or f"~/luthien-demo/{demo}"))
    elif cmd == "template-dir":
        print(str(DEMOS_DIR / demo / "template"))
    elif cmd == "surfaces":
        print(" ".join(sorted(manifest.get("surfaces", {}).keys())))
    elif cmd == "policy":
        if len(rest) != 3:
            _die("usage: _demo_manifest.py policy <demo> <state> <surface>")
        _, state, surface = rest
        print(json.dumps(_payload(manifest, state, surface)))
    else:
        _die(f"unknown command: {cmd}")


if __name__ == "__main__":
    main(sys.argv[1:])
