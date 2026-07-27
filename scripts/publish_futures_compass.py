#!/usr/bin/env python3
"""Refresh, validate, commit, and directly deploy the futures compass snapshot."""
from __future__ import annotations

import fcntl
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

try:
    from pages_release import release_pages
except ModuleNotFoundError:
    from scripts.pages_release import release_pages

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "public/data/futures-compass.json"
FUTURES_PYTHON = "/root/.cache/etf-futures/venv/bin/python"
LOCK = Path("/root/.hermes/state/futures-compass-publish.lock")
EXTERNAL_DIRTY = {"public/data/korea-tech-factor-shadow.json"}



def run(command: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=check, text=True, capture_output=True, **kwargs)


@contextmanager
def publish_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def is_ancestor(left: str, right: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", left, right], check=False).returncode == 0


def foreign_dirty_paths(lines: list[str]) -> list[str]:
    paths: list[str] = []
    for line in lines:
        path = line[3:].strip() if len(line) >= 4 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in EXTERNAL_DIRTY:
            paths.append(path)
    return paths


def restore_tracked_dist() -> None:
    for path in EXTERNAL_DIRTY:
        tracked = run(["git", "show", f"HEAD:{path}"]).stdout
        target = ROOT / "dist" / Path(path).relative_to("public")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(tracked, encoding="utf-8")


def preflight() -> None:
    if run(["git", "branch", "--show-current"]).stdout.strip() != "main":
        raise RuntimeError("futures publisher requires main branch")
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode:
        raise RuntimeError("futures publisher requires a clean git index")
    if run(["git", "diff", "--quiet", "--", SNAPSHOT], check=False).returncode:
        raise RuntimeError("futures snapshot already has uncommitted changes")
    dirty = run(["git", "status", "--porcelain", "--untracked-files=all"]).stdout.splitlines()
    foreign = foreign_dirty_paths(dirty)
    if foreign:
        raise RuntimeError(f"futures publisher requires a clean worktree; foreign dirty paths: {foreign}")
    run(["git", "fetch", "origin", "main"])
    if is_ancestor("HEAD", "origin/main"):
        run(["git", "merge", "--ff-only", "origin/main"])
    elif not is_ancestor("origin/main", "HEAD"):
        raise RuntimeError("main and origin/main diverged")


def publish(slot: str) -> dict[str, str]:
    with publish_lock():
        preflight()
        try:
            run([FUTURES_PYTHON, "scripts/run_futures_compass_maintenance.py", "--slot", slot])
            run([FUTURES_PYTHON, "scripts/validate_futures_compass.py"])
        except Exception:
            run(["git", "checkout", "--", SNAPSHOT], check=False)
            raise
        if run(["git", "diff", "--quiet", "--", SNAPSHOT], check=False).returncode == 0:
            return {"status": "unchanged", "slot": slot}
        run(["npm", "run", "build"])
        run(["git", "commit", "--only", "-m", f"data: refresh futures compass {slot}", "--", SNAPSHOT])
        run(["git", "fetch", "origin", "main"])
        if not is_ancestor("origin/main", "HEAD"):
            raise RuntimeError("origin/main changed during futures publication")
        run(["git", "push", "origin", "HEAD:main"])
        restore_tracked_dist()
        release_pages([
            "https://etf.peekabo.cc/futures-compass/",
            "https://etf.peekabo.cc/data/futures-compass.json",
        ])
        return {"status": "published", "slot": slot}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=("preopen", "day-close", "night"))
    args = parser.parse_args()
    print(json.dumps(publish(args.slot), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
