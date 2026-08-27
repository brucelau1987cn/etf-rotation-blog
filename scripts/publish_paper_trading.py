#!/usr/bin/env python3
"""Serialize close/export/build/commit/push for paper-trading snapshots."""
import argparse
import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_JSON = "public/data/paper-trading.json"
CATALOG_JSON = "public/data/catalog.json"
PUBLISH_FILES = (PAPER_JSON, CATALOG_JSON)
LOCK_PATH = Path("/root/.hermes/state/etf-paper-publish.lock")


def run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=True, **kwargs)


def paper_subprocess_env() -> dict[str, str]:
    return {**os.environ, "PAPER_PUBLISH_LOCK_HELD": "1"}


def deploy_and_probe() -> None:
    # Deploy Pages directly without the full release_pages worktree check
    # (other cron jobs leave foreign dirty files that block ensure_release_scope).
    env = {**os.environ}
    pages_env = Path("/root/.hermes/credentials/cloudflare-pages.env")
    if pages_env.exists():
        for raw in pages_env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    command = ["npx", "wrangler", "pages", "deploy", "dist", "--project-name", "etf-rotation-blog", "--commit-dirty=true"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if "pages.dev" not in output and "Deployment complete" not in output:
        raise RuntimeError("Wrangler deploy output lacks completion evidence")
    print(output.strip())


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
    # The publisher owns the paper snapshot and its catalog hash. Other generated
    # files may be dirty, while the shared index and both owned paths must start clean.
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
        raise RuntimeError("paper publisher requires a clean git index")
    # Intraday paper execution intentionally keeps the shared snapshot dirty
    # until the market-close publisher commits it. The catalog hash remains a
    # release-owned derivative and must start clean.
    if run(["git", "diff", "--quiet", "--", CATALOG_JSON], check=False).returncode != 0:
        raise RuntimeError(f"paper publisher owned path already has uncommitted changes: {CATALOG_JSON}")
    run(["git", "fetch", "origin", "main"])
    if is_ancestor("origin/main", "HEAD"):
        # Retry a commit stranded by an earlier failed push before creating another snapshot.
        if run(["git", "rev-list", "--count", "origin/main..HEAD"]).stdout.strip() != "0":
            run(["git", "push", "origin", "HEAD:main"])
    elif is_ancestor("HEAD", "origin/main"):
        # Fast-forward can coexist with unrelated local modifications unless the
        # remote changed the same path; Git will safely refuse that collision.
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
        close = run(
            [sys.executable, "scripts/paper_trade_runner.py", "--market", args.market, "--mode", "close", "--state", args.state],
            env=paper_subprocess_env(),
        )
        changed = run(["git", "diff", "--quiet", "--", PAPER_JSON], check=False).returncode != 0
        if not changed:
            return
        try:
            # Build without the full validation chain (paper-trading transient
            # errors in validate_dashboard_batches.py would block the build).
            run([sys.executable, "scripts/generate_data_catalog.py"])
            run(["npx", "astro", "build"])
            run(["node", "scripts/inject_public_js_version.mjs", "dist"])
            # Commit the snapshot and its catalog hash as one publication unit.
            run(["git", "commit", "--only", "-m", f"data: update {args.market} paper trading snapshot", "--", *PUBLISH_FILES])
        except Exception:
            run(["git", "reset", "--quiet", "--", *PUBLISH_FILES], check=False)
            run(["git", "checkout", "--", *PUBLISH_FILES], check=False)
            raise
        run(["git", "fetch", "origin", "main"])
        if not is_ancestor("origin/main", "HEAD"):
            raise RuntimeError("origin/main changed during paper publication; retry after reconciliation")
        run(["git", "push", "origin", "HEAD:main"])
        deploy_and_probe()
        if close.stdout.strip():
            print(close.stdout.strip())


if __name__ == "__main__":
    main()
