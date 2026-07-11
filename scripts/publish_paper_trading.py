#!/usr/bin/env python3
"""Serialize close/export/build/commit/push for paper-trading snapshots."""
import argparse
import fcntl
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_JSON = "public/data/paper-trading.json"
LOCK_PATH = Path("/root/.hermes/state/etf-paper-publish.lock")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=True)


@contextmanager
def publish_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def is_ancestor(left: str, right: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", left, right], check=False).returncode == 0


def sync_before_publish():
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"paper publisher requires main branch, got {branch!r}")
    if run(["git", "status", "--porcelain"]).stdout.strip():
        raise RuntimeError("paper publisher requires a clean worktree/index")
    run(["git", "fetch", "origin", "main"])
    if is_ancestor("origin/main", "HEAD"):
        # Retry a commit stranded by an earlier failed push before creating another snapshot.
        if run(["git", "rev-list", "--count", "origin/main..HEAD"]).stdout.strip() != "0":
            run(["git", "push", "origin", "HEAD:main"])
    elif is_ancestor("HEAD", "origin/main"):
        run(["git", "merge", "--ff-only", "origin/main"])
    else:
        raise RuntimeError("main and origin/main diverged; manual reconciliation required")


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

    with publish_lock():
        sync_before_publish()
        close = run([sys.executable, "scripts/paper_trade_runner.py", "--market", args.market, "--mode", "close", "--state", args.state])
        changed = run(["git", "diff", "--quiet", "--", PAPER_JSON], check=False).returncode != 0
        if not changed:
            return
        run(["npm", "run", "build"])
        # --only + pathspec prevents unrelated staged content from entering this commit.
        run(["git", "commit", "--only", "-m", f"data: update {args.market} paper trading snapshot", "--", PAPER_JSON])
        run(["git", "fetch", "origin", "main"])
        if not is_ancestor("origin/main", "HEAD"):
            raise RuntimeError("origin/main changed during paper publication; retry after reconciliation")
        run(["git", "push", "origin", "HEAD:main"])
        if close.stdout.strip():
            print(close.stdout.strip())


if __name__ == "__main__":
    main()
