#!/usr/bin/env python3
"""Canonical static-site build entry point.

Use this instead of composing `bootstrap_build_python.py` + `.build-venv/bin/python
scripts/...` + `npx astro build` by hand. It pins the interpreters through
`scripts/project_runtime.py`, strips the gateway virtualenv (PYTHONPATH is what
breaks `rpds`), and sets `PUBLIC_JS_ASSET_VERSION`.

    /usr/bin/python3 scripts/build_site.py                     # full build
    /usr/bin/python3 scripts/build_site.py --no-contract-chain  # publisher scope
    /usr/bin/python3 scripts/build_site.py --clean-dist
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from project_runtime import bootstrap_build_python, build_static_site  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static site with project interpreters.")
    parser.add_argument(
        "--no-contract-chain",
        action="store_true",
        help="skip the cross-market validator/generator chain (single-market publisher scope)",
    )
    parser.add_argument(
        "--clean-dist",
        action="store_true",
        help="remove dist/ before building",
    )
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="only create/refresh .build-venv",
    )
    args = parser.parse_args(argv)
    if args.bootstrap_only:
        bootstrap_build_python()
        print("build venv ready: .build-venv")
        return 0
    build_static_site(contract_chain=not args.no_contract_chain, clean_dist=args.clean_dist)
    print("build ready: dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())