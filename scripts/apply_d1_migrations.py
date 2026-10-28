#!/usr/bin/env python3
"""Auditable D1 migration entrypoint.

This wrapper is intentionally dry-run by default. Production application requires
an explicit --apply and invokes Wrangler against the database bound in wrangler.toml.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "etf-compass-auth"

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from pages_release import wrangler_spec  # noqa: E402  (single source for the pinned wrangler spec)


def command(apply: bool = False) -> list[str]:
    args = ["npx", "--yes", wrangler_spec(), "d1", "migrations", "apply", DATABASE, "--config", "wrangler.toml"]
    if not apply:
        args.append("--dry-run")
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply migrations to the configured D1 database")
    args = parser.parse_args()
    if args.apply:
        return subprocess.run(command(apply=True), cwd=ROOT, check=False).returncode
    print("DRY-RUN:", " ".join(command()))
    print("database:", DATABASE)
    print("migrations:", sorted(p.name for p in (ROOT / "migrations").glob("*.sql")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
