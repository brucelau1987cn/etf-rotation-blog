#!/usr/bin/env python3
"""Close, export and publish one paper-trading account snapshot."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_JSON = "public/data/paper-trading.json"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, check=True, text=True, capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["A", "US"])
    parser.add_argument("--state", default="/root/.hermes/state/etf-paper-trading.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        assert ROOT.joinpath("scripts/paper_trade_runner.py").exists()
        print("publish_paper_trading self-test: OK")
        return
    if not args.market:
        parser.error("--market is required")

    close = run([sys.executable, "scripts/paper_trade_runner.py", "--market", args.market, "--mode", "close", "--state", args.state])
    run(["npm", "run", "build"])
    run(["git", "add", PAPER_JSON])
    changed = subprocess.run(["git", "diff", "--cached", "--quiet", "--", PAPER_JSON], cwd=ROOT).returncode != 0
    if not changed:
        return
    run(["git", "commit", "-m", f"data: update {args.market} paper trading snapshot"])
    run(["git", "pull", "--rebase", "origin", "main"])
    run(["git", "push", "origin", "main"])
    if close.stdout.strip():
        print(close.stdout.strip())


if __name__ == "__main__":
    main()
